import html
import re
import threading
from datetime import date

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import (
    clear_property_telegram_chat_id,
    log_activity,
    publish_draft_timeline,
    set_property_telegram_chat_id,
    update_line_item_status,
)
from services.pm_digest import (
    clear_pm_chat_id,
    get_pm_chat_id,
    link_pm_chat,
    send_daily_pm_digest,
)
from services.telegram_bot import (
    PM_DIGEST_VERIFICATION_PHRASE,
    find_group_by_phrase,
    send_and_pin_portal_link,
    send_status_update_alert,
    send_timeline_published_alert,
    verification_phrase,
)
from services.timeline_drafter import (
    draft_timeline_from_notes,
    refine_timeline_draft,
    tasks_to_markdown_table,
)
from utils.status import normalize_status, status_color
from utils.units import sort_units_contingency_last

STATUS_OPTIONS = ["Pending", "In Progress", "Completed"]

# Positive match: only count a unit as a living unit if its name actually
# looks like a residential unit identifier. Requires "unit"/"apt"/etc. to be
# immediately followed by whitespace+identifier or a number, so plural/
# incidental mentions (e.g. "Rear Staircase — Units 2 & 3") don't match.
LIVING_UNIT_PATTERN = re.compile(
    r"\bunit\s+\w|\bunit\d|\b(apt|apartment|suite|floor)\b",
    re.IGNORECASE,
)


def _is_living_unit(unit_name: str) -> bool:
    return bool(LIVING_UNIT_PATTERN.search(unit_name))


def _render_timeline_sandbox(supabase, property_id: str, property_name: str):
    """Timeline Drafting Sandbox — shown instead of the normal KPI/task view
    when a property has no line_items yet. Lets a PM turn rough notes into
    an AI-sequenced draft, refine it in natural language, then publish it
    into the database as a real unit + line_items."""
    draft_key = f"draft_tasks_{property_id}"

    st.subheader("🧪 Timeline Builder")
    st.caption(
        f"No schedule yet for **{property_name}**. Draft one from rough notes "
        "before it goes live."
    )

    notes = st.text_area(
        "Rough notes from contractor discussions",
        height=180,
        key=f"draft_notes_{property_id}",
        placeholder=(
            "e.g. Full gut reno, 2 units. Demo everything first. New electrical "
            "panel. Kitchen cabinets are custom order, 3 week lead time. "
            "Bathrooms need full replumb..."
        ),
    )

    if st.button("Generate Draft Timeline", type="primary", key="generate_draft"):
        if not notes.strip():
            st.warning("Add some notes first.")
        else:
            with st.spinner("Asking Claude to sequence a draft timeline..."):
                try:
                    tasks = draft_timeline_from_notes(
                        property_name, notes, date.today()
                    )
                except Exception as e:
                    st.error(f"Draft generation failed: {e}")
                    tasks = None
            if tasks:
                st.session_state[draft_key] = tasks

    tasks = st.session_state.get(draft_key)
    if not tasks:
        return

    st.divider()
    st.markdown("#### Draft Timeline")
    st.markdown(tasks_to_markdown_table(tasks))

    st.markdown("#### Refine")
    col_text, col_audio = st.columns(2)
    with col_text:
        tweak = st.text_input(
            "Natural-language tweak",
            key=f"refine_text_{property_id}",
            placeholder="e.g. Add a 2-day buffer after framing",
        )
    with col_audio:
        audio_file = st.file_uploader(
            "Or upload a voice memo",
            type=["mp3", "wav", "m4a", "ogg"],
            key=f"refine_audio_{property_id}",
        )

    if st.button("Update Draft", key="update_draft"):
        if audio_file is not None:
            st.warning(
                "Voice memo transcription isn't wired up yet — Claude doesn't "
                "accept raw audio, and no speech-to-text service is "
                "configured. Please use the text tweak for now; the upload "
                "slot is ready for when that's added."
            )
        if tweak.strip():
            with st.spinner("Applying your change..."):
                try:
                    updated = refine_timeline_draft(property_name, tasks, tweak)
                except Exception as e:
                    st.error(f"Refinement failed: {e}")
                    updated = None
            if updated:
                st.session_state[draft_key] = updated
                st.rerun()
        elif audio_file is None:
            st.warning("Enter a tweak or upload a voice memo before updating.")

    st.divider()
    if st.button("🚀 Publish Timeline & Launch Gantt", type="primary"):
        with st.spinner("Publishing to the database..."):
            try:
                summary = publish_draft_timeline(
                    supabase, property_id, "General Scope", tasks
                )
            except Exception as e:
                st.error(f"Publish failed: {e}")
                return

        del st.session_state[draft_key]

        log_activity(
            supabase,
            property_id,
            "schedule",
            f"Timeline published ({summary['line_items']} tasks)",
        )

        chat_id = None
        try:
            prop_row = (
                supabase.table("properties")
                .select("telegram_chat_id")
                .eq("id", property_id)
                .execute()
                .data
            )
            if prop_row:
                chat_id = prop_row[0].get("telegram_chat_id")
        except Exception:
            pass

        threading.Thread(
            target=send_timeline_published_alert,
            kwargs=dict(
                property_name=property_name,
                property_id=property_id,
                tasks=tasks,
                chat_id=chat_id,
            ),
            daemon=True,
        ).start()
        threading.Thread(
            target=send_and_pin_portal_link,
            kwargs=dict(property_id=property_id, chat_id=chat_id),
            daemon=True,
        ).start()

        st.success(
            f"Published {summary['line_items']} tasks. Launching the Gantt chart..."
        )
        from utils.pages import SCHEDULE_PAGE

        st.switch_page(SCHEDULE_PAGE)


def render():
    st.title("Dashboard")

    supabase = get_supabase_client()
    try:
        properties = (
            supabase.table("properties")
            .select("id, property_name, telegram_chat_id")
            .order("property_name")
            .execute()
            .data
        )
    except Exception:
        # telegram_chat_id column not migrated onto properties yet — degrade
        # gracefully so the rest of the Dashboard still works; the Telegram
        # Group Sync section just won't have anything to link to yet.
        properties = (
            supabase.table("properties")
            .select("id, property_name")
            .order("property_name")
            .execute()
            .data
        )
        for p in properties:
            p["telegram_chat_id"] = None

    if not properties:
        st.info("No properties yet. Upload a SOW to get started.")
        return

    pm_chat_id = get_pm_chat_id(supabase)
    with st.expander("🔔 Daily PM Summary", expanded=not bool(pm_chat_id)):
        st.caption(
            "One Jeeves DM per day (not real-time — that would just "
            "duplicate each property's own group alerts) summarizing "
            "schedule changes, draw releases, journal activity, and "
            "material purchases across every property."
        )
        if pm_chat_id:
            st.success("Linked — you'll receive Jeeves' daily summary in this DM.")
            col_test, col_unlink = st.columns(2)
            if col_test.button("Send Test Summary Now", width="stretch"):
                with st.spinner("Building and sending your summary..."):
                    sent = send_daily_pm_digest(supabase)
                if sent:
                    st.success("Sent — check your DM with Jeeves.")
                else:
                    st.error("Couldn't reach Telegram. Try again.")
            if col_unlink.button("Unlink", width="stretch"):
                clear_pm_chat_id(supabase)
                st.success("Unlinked.")
                st.rerun()
        else:
            st.write(
                "Open a direct message with Jeeves (not a group) and send "
                "this exact phrase:"
            )
            st.code(PM_DIGEST_VERIFICATION_PHRASE)
            if st.button("Check for My Chat", key="link_pm_digest"):
                match = link_pm_chat(supabase)
                if match:
                    st.success("Splendid! Jeeves will now DM you daily.")
                    st.rerun()
                else:
                    st.warning(
                        "No matching message found yet — make sure you "
                        "messaged Jeeves directly with the exact phrase, "
                        "then try again."
                    )

    property_names = [p["property_name"] for p in properties]
    selected_name = st.selectbox("Property", property_names)
    selected_property = next(
        p for p in properties if p["property_name"] == selected_name
    )
    property_id = selected_property["id"]

    with st.expander(
        "🔗 Telegram Group Sync",
        expanded=not bool(selected_property.get("telegram_chat_id")),
    ):
        if selected_property.get("telegram_chat_id"):
            st.success(f"Linked to chat ID `{selected_property['telegram_chat_id']}`")
            if st.button("Unlink this group", key="unlink_telegram_group"):
                clear_property_telegram_chat_id(supabase, property_id)
                st.success("Unlinked. This property has no Telegram group for now.")
                st.rerun()
        phrase = verification_phrase(selected_name)
        st.write(
            "Add Jeeves to your new Telegram group, then send this exact "
            "message in the group:"
        )
        st.code(phrase)
        if st.button("Check for Group Link", key="sync_telegram_group"):
            match = find_group_by_phrase(phrase)
            if match:
                try:
                    set_property_telegram_chat_id(
                        supabase, property_id, match["chat_id"]
                    )
                except Exception:
                    st.error(
                        "Found the group, but properties.telegram_chat_id "
                        "doesn't exist in the database yet — run the "
                        "migration first, then try again."
                    )
                else:
                    st.success(
                        "Splendid! Jeeves has successfully linked to this "
                        "group chat."
                    )
                    st.rerun()
            else:
                st.warning(
                    "No matching message found yet — make sure you've added "
                    "Jeeves and sent the exact phrase, then try again."
                )

    units = (
        supabase.table("units")
        .select("id, unit_name, telegram_chat_id")
        .eq("property_id", property_id)
        .order("unit_name")
        .execute()
        .data
    )
    units = sort_units_contingency_last(units)

    unit_ids = [u["id"] for u in units]
    line_items = []
    if unit_ids:
        line_items = (
            supabase.table("line_items")
            .select(
                "id, unit_id, task_name, cost_group, budgeted_cost, status, "
                "start_date, estimated_end_date, dependencies"
            )
            .in_("unit_id", unit_ids)
            .execute()
            .data
        )

    if not line_items:
        _render_timeline_sandbox(supabase, property_id, selected_name)
        return

    # --- KPIs ---
    total_budget = sum(item.get("budgeted_cost") or 0 for item in line_items)
    total_items = len(line_items)
    completed_items = sum(
        1
        for item in line_items
        if (item.get("status") or "").strip().lower() in ("completed", "complete")
    )
    progress_pct = (completed_items / total_items * 100) if total_items else 0
    living_units_count = sum(1 for u in units if _is_living_unit(u["unit_name"]))

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Budget", f"${total_budget:,.0f}")
    col2.metric("Progress", f"{progress_pct:.0f}%")
    col2.progress(progress_pct / 100)
    col3.metric("Living Units", living_units_count)

    st.divider()

    # --- Unit / area selector (includes non-living areas like Exterior/Basement) ---
    unit_names = [u["unit_name"] for u in units]
    selected_unit_name = st.selectbox("Select Unit / Area", unit_names)
    selected_unit = next(u for u in units if u["unit_name"] == selected_unit_name)

    st.subheader(selected_unit_name)

    unit_items = [item for item in line_items if item["unit_id"] == selected_unit["id"]]

    if not unit_items:
        st.caption("No line items for this unit.")
        return

    grouped: dict[str, list] = {}
    for item in unit_items:
        group = item.get("cost_group") or "Uncategorized"
        grouped.setdefault(group, []).append(item)

    for group_name, items in grouped.items():
        st.markdown(f"**{html.escape(group_name)}**")
        for item in items:
            current_status = normalize_status(item.get("status"))
            color = status_color(current_status)
            cost = item.get("budgeted_cost") or 0
            task_name = html.escape(item["task_name"])

            col_task, col_status = st.columns([2.2, 1], vertical_alignment="center")
            with col_task:
                st.markdown(
                    f"""
                    <div style="padding:0.4rem 0;">
                        <span style="display:inline-block;width:8px;height:8px;
                                     border-radius:50%;background:{color};
                                     margin-right:6px;"></span>
                        <span style="font-weight:600;">{task_name}</span>
                        <div style="font-size:0.85rem;opacity:0.7;margin-left:14px;">
                            ${cost:,.2f}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with col_status:
                new_status = st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(current_status),
                    key=f"status_{item['id']}",
                    label_visibility="collapsed",
                )

            if new_status != current_status:
                update_line_item_status(supabase, item["id"], new_status)
                log_activity(
                    supabase,
                    property_id,
                    "schedule",
                    f"{selected_unit_name}: {item['task_name']} — "
                    f"{current_status} → {new_status}",
                )
                threading.Thread(
                    target=send_status_update_alert,
                    kwargs=dict(
                        property_name=selected_name,
                        unit_name=selected_unit_name,
                        task_name=item["task_name"],
                        old_status=current_status,
                        new_status=new_status,
                        cost=cost,
                        chat_id=(
                            selected_unit.get("telegram_chat_id")
                            or selected_property.get("telegram_chat_id")
                        ),
                    ),
                    daemon=True,
                ).start()
                st.rerun()
        st.write("")
