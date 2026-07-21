import html
import threading
from datetime import timedelta

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import apply_schedule_adjustments, log_activity
from services.telegram_bot import (
    send_and_pin_batch_schedule_update,
    send_and_pin_portal_link,
    send_schedule_adjustment_alert,
)
from services.timeline_drafter import (
    active_tasks_from_line_items,
    refine_timeline_draft,
    tasks_to_markdown_table,
)
from utils.gantt_chart import prepare_gantt_dataframe, render_responsive_gantt_chart
from utils.mobile import inject_mobile_button_css
from utils.status import status_color
from utils.units import sort_units_contingency_last


def _format_quick_edit_change(
    label: str,
    original_start,
    original_end,
    original_percent: int,
    new_start,
    new_end,
    new_percent: int,
) -> str | None:
    """Describes what actually changed in a quick edit, not the whole
    record — e.g. "extended 3 days, new ETC 08-31-2026" or "now 35%
    complete" — for the consolidated Telegram publish message. Returns
    None if nothing actually changed."""
    parts = []
    if new_start != original_start or new_end != original_end:
        duration_delta = (new_end - original_end).days
        new_end_str = new_end.strftime("%m-%d-%Y")
        if duration_delta > 0:
            day_word = "day" if duration_delta == 1 else "days"
            parts.append(f"extended {duration_delta} {day_word}, new ETC {new_end_str}")
        elif duration_delta < 0:
            day_word = "day" if abs(duration_delta) == 1 else "days"
            parts.append(
                f"pulled in {abs(duration_delta)} {day_word}, new ETC {new_end_str}"
            )
        else:
            parts.append(f"start moved to {new_start.strftime('%m-%d-%Y')}")

    if new_percent != original_percent:
        parts.append(f"now {new_percent}% complete")

    if not parts:
        return None
    return f"{label}: {', '.join(parts)}"


def render():
    st.title("Schedule")
    inject_mobile_button_css()

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

    selected_property_name = st.selectbox(
        "Property", [p["property_name"] for p in properties]
    )
    selected_property = next(
        p for p in properties if p["property_name"] == selected_property_name
    )
    property_id = selected_property["id"]

    units = (
        supabase.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .order("unit_name")
        .execute()
        .data
    )

    if not units:
        st.info("No units for this property yet.")
        return

    units = sort_units_contingency_last(units)
    unit_id_to_name = {u["id"]: u["unit_name"] for u in units}

    unit_options = ["All Units"] + [u["unit_name"] for u in units]
    selected_unit_name = st.selectbox("Select Unit / Area", unit_options)

    if selected_unit_name == "All Units":
        unit_ids = list(unit_id_to_name.keys())
    else:
        unit_ids = [
            u["id"] for u in units if u["unit_name"] == selected_unit_name
        ]

    items = (
        supabase.table("line_items")
        .select(
            "id, unit_id, task_name, cost_group, status, percent_complete, "
            "start_date, estimated_end_date, dependencies"
        )
        .in_("unit_id", unit_ids)
        .execute()
        .data
    )

    if not items:
        st.info("No scheduled tasks yet for this selection.")
        return

    id_to_task_name = {item["id"]: item["task_name"] for item in items}

    df, chronological_order, skipped_count = prepare_gantt_dataframe(
        items,
        unit_id_to_name=unit_id_to_name,
        label_with_unit=(selected_unit_name == "All Units"),
    )
    if skipped_count:
        st.caption(
            f"{skipped_count} task(s) skipped from the timeline — missing "
            "start/end dates."
        )
    if df.empty:
        st.info("None of the tasks in this selection have valid dates yet.")
        return

    show_chart = st.checkbox("Show Gantt chart", value=True)

    pending_key = f"pending_changes_{property_id}"
    if pending_key not in st.session_state:
        st.session_state[pending_key] = []
    pending_changes = st.session_state[pending_key]

    publish_col, _ = st.columns([1, 2])
    with publish_col:
        if pending_changes:
            if st.button(
                "Publish Updates to Telegram 🚀",
                type="primary",
                key="publish_pending_changes",
                width="stretch",
            ):
                with st.spinner("Notifying the team..."):
                    sent = send_and_pin_batch_schedule_update(
                        property_name=selected_property_name,
                        property_id=property_id,
                        changes=pending_changes,
                        chat_id=selected_property.get("telegram_chat_id"),
                    )
                if sent:
                    for change in pending_changes:
                        log_activity(supabase, property_id, "schedule", change)
                    st.session_state[pending_key] = []
                    st.success("Published to Telegram.")
                    st.rerun()
                else:
                    st.error(
                        "Couldn't reach Telegram — changes are still "
                        "pending, try again."
                    )
        else:
            st.button(
                "Schedule Up to Date",
                disabled=True,
                key="publish_pending_changes_disabled",
                width="stretch",
            )

    if show_chart:
        st.markdown(
            """
            <style>
            .st-key-quick_edit_overlay {
                position: fixed;
                top: 90px;
                right: 68px;
                z-index: 50;
                width: 260px;
                background: #FFFFFF;
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: 12px;
                padding: 1rem;
                box-shadow: 0 8px 24px rgba(0,0,0,0.18);
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        event = render_responsive_gantt_chart(
            df, chronological_order, key_prefix=f"sched_{property_id}_{selected_unit_name}"
        )
        selected_points = event.selection.points if event else []
        if selected_points:
            clicked_id = selected_points[0]["customdata"][0]
            clicked_rows = df[df["id"] == clicked_id]
            if not clicked_rows.empty:
                row = clicked_rows.iloc[0]
                original_duration_days = (
                    row["estimated_end_date"] - row["start_date"]
                ).days

                start_key = f"quick_start_{clicked_id}"
                end_key = f"quick_end_{clicked_id}"
                prev_start_key = f"quick_start_prev_{clicked_id}"

                if start_key not in st.session_state:
                    st.session_state[start_key] = row["start_date"].date()
                if end_key not in st.session_state:
                    st.session_state[end_key] = row["estimated_end_date"].date()
                if prev_start_key not in st.session_state:
                    st.session_state[prev_start_key] = st.session_state[start_key]

                with st.container(key="quick_edit_overlay"):
                    st.markdown("**Quick edit**")
                    st.caption(row["label"])
                    new_start = st.date_input("Start date", key=start_key)

                    # Start date changed since last run — shift the end
                    # date to keep the same duration, unless the user
                    # is the one who just changed the end date directly.
                    if st.session_state[prev_start_key] != new_start:
                        st.session_state[end_key] = new_start + timedelta(
                            days=original_duration_days
                        )
                        st.session_state[prev_start_key] = new_start

                    new_end = st.date_input("End date", key=end_key)

                    percent_key = f"quick_percent_{clicked_id}"
                    if percent_key not in st.session_state:
                        st.session_state[percent_key] = int(
                            row.get("percent_complete") or 0
                        )
                    new_percent = st.number_input(
                        "Actual % complete",
                        min_value=0,
                        max_value=100,
                        step=1,
                        key=percent_key,
                        help=(
                            "This is what draw milestones on the Budget "
                            "page check against — keep it current."
                        ),
                    )

                    if st.button("Update Task", key=f"quick_update_{clicked_id}"):
                        if new_end < new_start:
                            st.error(
                                "End date must be on or after the start date."
                            )
                        else:
                            update_payload = {
                                "start_date": new_start.isoformat(),
                                "estimated_end_date": new_end.isoformat(),
                                "percent_complete": new_percent,
                            }
                            # Percent is the single source of truth for
                            # status whenever it's touched here —
                            # always fully in sync in both directions,
                            # including un-completing on a correction
                            # (e.g. 100% hit by mistake, dialed back
                            # down): 100% -> Completed, 0% -> Pending,
                            # anything in between -> In Progress.
                            if new_percent == 100:
                                update_payload["status"] = "Completed"
                            elif new_percent == 0:
                                update_payload["status"] = "Pending"
                            else:
                                update_payload["status"] = "In Progress"

                            supabase.table("line_items").update(
                                update_payload
                            ).eq("id", clicked_id).execute()
                            change_desc = _format_quick_edit_change(
                                row["label"],
                                row["start_date"].date(),
                                row["estimated_end_date"].date(),
                                int(row.get("percent_complete") or 0),
                                new_start,
                                new_end,
                                new_percent,
                            )
                            if change_desc:
                                st.session_state[pending_key].append(change_desc)
                            del st.session_state[start_key]
                            del st.session_state[end_key]
                            del st.session_state[prev_start_key]
                            del st.session_state[percent_key]
                            st.success("Updated. Publish when ready to notify the team.")
                            st.rerun()

    with st.expander("🔧 Adjust Active Timeline"):
        adjust_key = f"adjust_tasks_{property_id}"
        instruction = st.text_input(
            "Natural-language change",
            key=f"adjust_text_{property_id}",
            placeholder="e.g. Push framing start date back by 4 days",
        )
        if st.button(
            "Preview Adjustment", key="preview_adjustment", width="stretch"
        ):
            if not instruction.strip():
                st.warning("Enter a change first.")
            else:
                # Adjustments always operate on the whole property (not just
                # the unit currently selected for viewing), so dates stay
                # consistent regardless of which scope you're looking at.
                all_items = (
                    supabase.table("line_items")
                    .select(
                        "id, unit_id, task_name, cost_group, status, "
                        "start_date, estimated_end_date, dependencies"
                    )
                    .in_("unit_id", list(unit_id_to_name.keys()))
                    .execute()
                    .data
                )
                current_tasks = active_tasks_from_line_items(
                    all_items, unit_id_to_name=unit_id_to_name
                )
                with st.spinner("Asking Claude to apply and cascade the change..."):
                    try:
                        updated = refine_timeline_draft(
                            selected_property_name, current_tasks, instruction
                        )
                    except Exception as e:
                        st.error(f"Adjustment failed: {e}")
                        updated = None
                if updated:
                    st.session_state[adjust_key] = updated

        preview_tasks = st.session_state.get(adjust_key)
        if preview_tasks:
            st.markdown("#### Revised Schedule Preview")
            st.markdown(tasks_to_markdown_table(preview_tasks))

            if st.button(
                "Apply Schedule Adjustments",
                type="primary",
                key="apply_adjustment",
                width="stretch",
            ):
                with st.spinner("Updating Supabase..."):
                    try:
                        result = apply_schedule_adjustments(
                            supabase, property_id, preview_tasks
                        )
                    except Exception as e:
                        st.error(f"Failed to apply adjustments: {e}")
                    else:
                        del st.session_state[adjust_key]
                        log_activity(
                            supabase,
                            property_id,
                            "schedule",
                            f"AI adjustment: {instruction} "
                            f"(updated {result['updated']}, added "
                            f"{result['inserted']}, removed "
                            f"{result['deleted']}, renamed "
                            f"{result['renamed']})",
                        )
                        threading.Thread(
                            target=send_schedule_adjustment_alert,
                            kwargs=dict(
                                property_name=selected_property_name,
                                property_id=property_id,
                                instruction=instruction,
                                chat_id=selected_property.get("telegram_chat_id"),
                            ),
                            daemon=True,
                        ).start()
                        threading.Thread(
                            target=send_and_pin_portal_link,
                            kwargs=dict(
                                property_id=property_id,
                                chat_id=selected_property.get("telegram_chat_id"),
                            ),
                            daemon=True,
                        ).start()
                        st.success(
                            f"Updated {result['updated']}, added "
                            f"{result['inserted']}, removed "
                            f"{result['deleted']}, renamed "
                            f"{result['renamed']} task(s)."
                        )
                        st.rerun()

    st.divider()
    st.subheader("Timeline Feed")

    # Group into project-relative weeks (Week 1 = the 7 days starting from
    # the earliest start_date in this selection), rather than one flat list
    # with a per-task date badge.
    baseline = df["start_date"].min()
    df = df.copy()
    df["week_number"] = ((df["start_date"] - baseline).dt.days // 7) + 1

    for week_number, week_df in df.groupby("week_number"):
        week_start = baseline + timedelta(days=int((week_number - 1) * 7))
        week_end = week_start + timedelta(days=6)
        st.markdown(
            f"##### Week {int(week_number)} — "
            f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d')}"
        )

        for _, row in week_df.iterrows():
            status = row["status"] or "Pending"
            color = status_color(status)
            task_label = html.escape(row["label"])

            pill_text = status
            if status.strip().lower() == "in progress":
                percent = row.get("percent_complete") or 0
                if percent:
                    pill_text = f"{status} ({percent:.0f}%)"

            st.markdown(
                f"""
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:0.5rem 0;border-bottom:1px solid rgba(128,128,128,0.15);
                            gap:0.75rem;">
                    <div style="font-weight:600;">➜ {task_label}</div>
                    <div style="background:{color};color:white;padding:2px 10px;
                                border-radius:999px;font-size:0.75rem;font-weight:600;
                                white-space:nowrap;flex-shrink:0;">{html.escape(pill_text)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            dependencies = row.get("dependencies") or []
            dep_names = [
                id_to_task_name[d] for d in dependencies if d in id_to_task_name
            ]
            if dep_names:
                st.caption("🔗 Depends on: " + ", ".join(dep_names))

        st.write("")
