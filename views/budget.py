import threading

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import (
    assign_material_log_unit,
    assign_material_log_property,
    check_unit_overrun,
    create_draw_milestone,
    create_material_log,
    delete_draw_milestone,
    get_line_items_with_labels,
    get_materials_spend_by_unit,
    get_milestone_task_progress,
    get_unit_budget_comparison,
    log_activity,
    milestone_is_eligible,
    release_draw_milestone,
    set_budget_includes_materials,
)
from services.email_receipts import sync_email_receipts
from services.receipt_parser import (
    match_property_from_text,
    match_unit_from_reference,
    parse_receipt_text,
)
from services.telegram_bot import send_cost_overrun_alert, send_draw_release_alert
from utils.mobile import inject_mobile_button_css, inject_mobile_card_css


def _fire_overrun_alert_if_crossed(supabase, unit_id: str | None) -> None:
    """Best-effort: checks whether filing this purchase under a unit just
    pushed its labor + materials budget past the alert threshold, and if
    so fires the Telegram alert in the background. No-ops silently on any
    failure — a missed alert shouldn't block saving/assigning a purchase."""
    if not unit_id:
        return
    try:
        overrun = check_unit_overrun(supabase, unit_id)
    except Exception:
        return
    if not overrun:
        return
    threading.Thread(
        target=send_cost_overrun_alert,
        kwargs=dict(
            property_name=overrun["property_name"],
            unit_name=overrun["unit_name"],
            budgeted_cost=overrun["budgeted_cost"],
            spent=overrun["spent"],
            percent=overrun["percent"],
            chat_id=overrun["chat_id"],
        ),
        daemon=True,
    ).start()


def render():
    st.title("💰 Budget Draw Control")
    inject_mobile_button_css()
    inject_mobile_card_css(["milestone_card_", "unassigned_card_"])

    supabase = get_supabase_client()
    try:
        properties = (
            supabase.table("properties")
            .select("id, property_name, telegram_chat_id, archived")
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
            p["archived"] = False

    if not properties:
        st.info("No properties yet. Upload a SOW to get started.")
        return

    selected_name = st.selectbox("Property", [p["property_name"] for p in properties])
    selected_property = next(
        p for p in properties if p["property_name"] == selected_name
    )
    property_id = selected_property["id"]
    is_archived = bool(selected_property.get("archived"))

    if is_archived:
        st.info(
            "🔒 This project is finished and read-only — milestone actions "
            "are disabled. Reopen it from the Dashboard to make changes again."
        )

    units = (
        supabase.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .execute()
        .data
    )
    unit_ids = [u["id"] for u in units]
    line_items = []
    if unit_ids:
        line_items = (
            supabase.table("line_items")
            .select("id, unit_id, task_name, budgeted_cost")
            .in_("unit_id", unit_ids)
            .execute()
            .data
        )
    line_item_labels = get_line_items_with_labels(supabase, property_id)

    try:
        milestones = (
            supabase.table("draw_milestones")
            .select("id, milestone_name, draw_amount, status, released_at")
            .eq("property_id", property_id)
            .order("created_at")
            .execute()
            .data
        )
        for m in milestones:
            m["task_progress"] = get_milestone_task_progress(supabase, m["id"])
    except Exception:
        st.error(
            "The database doesn't have the expected draw-tracking tables/"
            "columns yet — run scripts/migration_draw_and_journal.sql and "
            "scripts/migration_per_task_draw_requirements.sql via "
            "Supabase's SQL Editor, then refresh."
        )
        return

    # --- KPIs ---
    total_budgeted = sum(item.get("budgeted_cost") or 0 for item in line_items)
    total_released = sum(
        m.get("draw_amount") or 0 for m in milestones if m.get("status") == "Released"
    )
    pending = [m for m in milestones if m.get("status") != "Released"]
    next_milestone = pending[0] if pending else None

    try:
        materials_total = sum(
            log.get("amount") or 0
            for log in supabase.table("material_logs")
            .select("amount")
            .eq("property_id", property_id)
            .execute()
            .data
        )
    except Exception:
        materials_total = None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "SOW Budget",
        f"${total_budgeted:,.0f}",
        help=(
            "Labor for most tasks. Tasks quoted as labor AND materials "
            "(e.g. a roof) include materials — mark those below."
        ),
    )
    col2.metric(
        "Materials Logged",
        f"${materials_total:,.0f}" if materials_total is not None else "—",
        help="Material purchases, tracked separately from the SOW labor budget.",
    )
    col3.metric("Total Funds Released", f"${total_released:,.0f}")
    col4.metric(
        "Next Upcoming Draw",
        f"${next_milestone['draw_amount']:,.0f}" if next_milestone else "—",
        help=next_milestone["milestone_name"] if next_milestone else None,
    )

    st.divider()

    st.subheader("🧾 Materials by Unit")
    st.caption(
        "Materials are tracked as materials, separate from the SOW budget "
        "(which is labor, except tasks marked below as labor + materials). "
        "Each purchase is filed under the unit named on the receipt; only "
        "units with labor + materials tasks are compared against a budget."
    )
    variance_migration_missing = False
    spend = {"units": [], "unassigned_spent": 0, "unassigned_count": 0}
    budget_by_unit = {}
    try:
        spend = get_materials_spend_by_unit(supabase, property_id)
        budget_by_unit = {
            r["unit_id"]: r for r in get_unit_budget_comparison(supabase, property_id)
        }
    except Exception:
        variance_migration_missing = True
        st.caption(
            "Run scripts/migration_material_unit.sql and "
            "scripts/migration_budget_includes_materials.sql via Supabase's "
            "SQL Editor to enable per-unit material tracking."
        )
    if spend["units"]:
        for row in spend["units"]:
            cols = st.columns([2.2, 1.2, 1.6, 1])
            cols[0].markdown(f"**{row['unit_name']}**")
            noun = "purchase" if row["count"] == 1 else "purchases"
            cols[1].caption(f"Spent ${row['spent']:,.0f} ({row['count']} {noun})")
            budget_row = budget_by_unit.get(row["unit_id"])
            if budget_row:
                cols[2].caption(
                    f"Budget (labor + materials) ${budget_row['budgeted_cost']:,.0f}"
                )
                if budget_row["variance"] < 0:
                    cols[3].error(f"−${abs(budget_row['variance']):,.0f}")
                else:
                    cols[3].success(f"+${budget_row['variance']:,.0f}")
    elif not variance_migration_missing:
        st.caption("No purchases have been filed under a unit yet.")
    if spend["unassigned_count"]:
        st.caption(
            f"${spend['unassigned_spent']:,.0f} in {spend['unassigned_count']} "
            "purchase(s) not yet filed under a unit — see below."
        )

    if not variance_migration_missing:
        try:
            unitless_logs = [
                log
                for log in (
                    supabase.table("material_logs")
                    .select("id, store, amount, purchase_date, unit_id, line_item_id")
                    .eq("property_id", property_id)
                    .order("purchase_date", desc=True)
                    .execute()
                    .data
                )
                if not log.get("unit_id") and not log.get("line_item_id")
            ]
        except Exception:
            unitless_logs = []
        if unitless_logs and units:
            with st.expander(
                f"🧮 Which unit was this for? ({len(unitless_logs)} unassigned)"
            ):
                st.caption(
                    "Tip: ask the contractor to write the unit on the receipt "
                    "(e.g. \"809 Fred Unit 3 kitchen\") and these file "
                    "themselves."
                )
                for log in unitless_logs:
                    st.markdown(f"**{log['store']}** — ${log['amount']:,.2f}")
                    st.caption(str(log.get("purchase_date") or "")[:10])
                    unit_choice = st.selectbox(
                        "Unit",
                        ["(unassigned)"] + [u["unit_name"] for u in units],
                        key=f"assign_unit_{log['id']}",
                        label_visibility="collapsed",
                        disabled=is_archived,
                    )
                    if unit_choice != "(unassigned)":
                        chosen_unit = next(
                            u["id"] for u in units if u["unit_name"] == unit_choice
                        )
                        assign_material_log_unit(supabase, log["id"], chosen_unit)
                        _fire_overrun_alert_if_crossed(supabase, chosen_unit)
                        st.rerun()

    if not variance_migration_missing:
        with st.expander("⚙️ Tasks whose budget includes materials"):
            st.caption(
                "Most SOW budgets are labor only. Mark tasks quoted as "
                "labor AND materials (e.g. a roof) so their material "
                "purchases are compared against the budget and can trigger "
                "overrun alerts. Everything else is just logged as materials."
            )
            label_by_id = {row["id"]: row["label"] for row in line_item_labels}
            flagged_ids = (
                {
                    i["id"]
                    for i in supabase.table("line_items")
                    .select("id")
                    .in_("unit_id", unit_ids)
                    .eq("budget_includes_materials", True)
                    .execute()
                    .data
                }
                if unit_ids
                else set()
            )
            chosen_labels = st.multiselect(
                "Labor + materials tasks",
                [row["label"] for row in line_item_labels],
                default=[label_by_id[i] for i in flagged_ids if i in label_by_id],
                key=f"materials_budget_{property_id}",
                disabled=is_archived,
            )
            if st.button(
                "Save",
                key=f"save_materials_budget_{property_id}",
                disabled=is_archived,
            ):
                chosen_ids = [
                    row["id"] for row in line_item_labels if row["label"] in chosen_labels
                ]
                other_ids = [
                    row["id"] for row in line_item_labels if row["id"] not in chosen_ids
                ]
                set_budget_includes_materials(supabase, chosen_ids, True)
                set_budget_includes_materials(supabase, other_ids, False)
                st.success("Saved.")
                st.rerun()

    st.divider()

    st.subheader("Milestones")
    if not milestones:
        st.caption("No draw milestones yet — add one below.")

    for m in milestones:
        with st.container(border=True, key=f"milestone_card_{m['id']}"):
            col_info, col_action = st.columns([3, 1])
            with col_info:
                st.markdown(f"**{m['milestone_name']}**")
                for t in m["task_progress"]:
                    met = t["actual_percent"] >= t["required_percent"]
                    icon = "✅" if met else "⏳"
                    st.caption(
                        f"{icon} {t['task_name']} — "
                        f"{t['actual_percent']:.0f}% / requires "
                        f"{t['required_percent']:.0f}%"
                    )
                if not m["task_progress"]:
                    st.caption(
                        "⚠️ No tasks linked — nothing to verify progress "
                        "against, so this can never become eligible."
                    )
                st.write(f"${m['draw_amount']:,.2f}")
                if m["status"] == "Released":
                    st.success(f"Released {m['released_at']}")
                else:
                    st.info("Pending")
            with col_action:
                if m["status"] != "Released":
                    eligible = milestone_is_eligible(m["task_progress"])
                    if not eligible:
                        st.caption("⚠️ Not all task thresholds met yet")
                    if st.button(
                        "Authorize Draw Release",
                        key=f"release_{m['id']}",
                        width="stretch",
                        disabled=is_archived,
                    ):
                        release_draw_milestone(supabase, m["id"])
                        log_activity(
                            supabase,
                            property_id,
                            "draw",
                            f"{m['milestone_name']} released — "
                            f"${m['draw_amount']:,.2f}",
                        )
                        threading.Thread(
                            target=send_draw_release_alert,
                            kwargs=dict(
                                property_name=selected_name,
                                milestone_name=m["milestone_name"],
                                draw_amount=m["draw_amount"],
                                chat_id=selected_property.get("telegram_chat_id"),
                            ),
                            daemon=True,
                        ).start()
                        st.success("Draw released.")
                        st.rerun()

                confirm_key = f"confirm_delete_{m['id']}"
                if st.session_state.get(confirm_key):
                    st.warning("Delete this milestone permanently?")
                    col_yes, col_no = st.columns(2)
                    if col_yes.button(
                        "Yes, delete", key=f"confirm_yes_{m['id']}", width="stretch"
                    ):
                        delete_draw_milestone(supabase, m["id"])
                        del st.session_state[confirm_key]
                        st.success("Milestone deleted.")
                        st.rerun()
                    if col_no.button(
                        "Cancel", key=f"confirm_no_{m['id']}", width="stretch"
                    ):
                        del st.session_state[confirm_key]
                        st.rerun()
                elif st.button(
                    "🗑️ Delete Milestone",
                    key=f"delete_{m['id']}",
                    width="stretch",
                    disabled=is_archived,
                ):
                    st.session_state[confirm_key] = True
                    st.rerun()

    st.divider()
    with st.expander("➕ Add Milestone"):
        milestone_name = st.text_input(
            "Milestone name", placeholder="e.g. Framing Complete"
        )
        draw_amount = st.number_input("Draw amount ($)", min_value=0.0, step=100.0)
        linked_choices = st.multiselect(
            "Link to task(s)",
            [item["task_name"] for item in line_items],
            help=(
                "Each linked task gets its own required % below. Actual "
                "progress is tracked per-task on the Schedule page."
            ),
        )

        task_requirements = []
        for task_name in linked_choices:
            item_id = next(
                item["id"] for item in line_items if item["task_name"] == task_name
            )
            required_percent = st.slider(
                f"Required % for: {task_name}",
                min_value=0,
                max_value=100,
                value=100,
                key=f"required_pct_{item_id}",
            )
            task_requirements.append(
                {"line_item_id": item_id, "required_percent": required_percent}
            )

        if st.button(
            "Add Milestone", type="primary", width="stretch", disabled=is_archived
        ):
            if not milestone_name.strip():
                st.warning("Enter a milestone name first.")
            else:
                create_draw_milestone(
                    supabase,
                    property_id,
                    milestone_name,
                    draw_amount,
                    task_requirements,
                )
                st.success("Milestone added.")
                st.rerun()

    st.divider()
    with st.expander("📥 Import Digital Receipts"):
        receipt_text = st.text_area(
            "Paste the raw text of a Home Depot Pro Xtra or Lowe's Pro "
            "e-receipt",
            height=200,
            key="receipt_paste_text",
        )
        if st.button(
            "Parse Receipt", type="primary", key="parse_receipt", width="stretch"
        ):
            if not receipt_text.strip():
                st.warning("Paste a receipt first.")
            else:
                with st.spinner("Asking Claude to extract the receipt details..."):
                    try:
                        parsed = parse_receipt_text(receipt_text)
                    except Exception as e:
                        st.error(f"Parsing failed: {e}")
                        parsed = None
                if parsed:
                    st.session_state["parsed_receipt"] = parsed
                    st.session_state["parsed_receipt_raw"] = receipt_text

        parsed = st.session_state.get("parsed_receipt")
        if parsed:
            st.markdown(f"**{parsed.get('store_name', 'Unknown store')}**")
            st.write(f"Date: {parsed.get('purchase_date', '—')}")
            st.write(f"Total: ${parsed.get('total_cost', 0):,.2f}")
            for li in parsed.get("line_items", []):
                st.caption(f"• {li.get('description')} — ${li.get('cost', 0):,.2f}")

            matched_id = match_property_from_text(
                st.session_state.get("parsed_receipt_raw", ""), properties
            )
            matched_name = next(
                (p["property_name"] for p in properties if p["id"] == matched_id),
                None,
            )
            if matched_name:
                st.success(f"Auto-matched to property: {matched_name}")
            else:
                st.warning(
                    "No property identifier found in the text — this will "
                    "go into the Unassigned Materials queue below."
                )

            unit_choice_id = None
            if matched_id:
                candidate_units = (
                    units
                    if matched_id == property_id
                    else supabase.table("units")
                    .select("id, unit_name")
                    .eq("property_id", matched_id)
                    .execute()
                    .data
                )
                if candidate_units and "suggested_unit_id" not in st.session_state:
                    with st.spinner("Reading the job name for a unit..."):
                        st.session_state["suggested_unit_id"] = (
                            match_unit_from_reference(
                                parsed.get("job_or_property_reference")
                                or st.session_state.get("parsed_receipt_raw", ""),
                                candidate_units,
                            )
                        )
                unit_options = ["(not filed under a unit)"] + [
                    u["unit_name"] for u in candidate_units
                ]
                suggested_name = next(
                    (
                        u["unit_name"]
                        for u in candidate_units
                        if u["id"] == st.session_state.get("suggested_unit_id")
                    ),
                    None,
                )
                if suggested_name:
                    st.info(f"Job name on the receipt points to: {suggested_name}")
                unit_label_choice = st.selectbox(
                    "File under unit",
                    unit_options,
                    index=unit_options.index(suggested_name) if suggested_name else 0,
                    key="receipt_unit_choice",
                )
                if unit_label_choice != "(not filed under a unit)":
                    unit_choice_id = next(
                        u["id"]
                        for u in candidate_units
                        if u["unit_name"] == unit_label_choice
                    )

            if st.button("Save Receipt", key="save_receipt", width="stretch"):
                create_material_log(
                    supabase,
                    store=parsed.get("store_name", "Unknown"),
                    amount=parsed.get("total_cost", 0),
                    property_id=matched_id,
                    purchase_date=parsed.get("purchase_date"),
                    receipt_details=st.session_state.get("parsed_receipt_raw"),
                    source="manual",
                    line_items_json=parsed.get("line_items"),
                    unit_id=unit_choice_id,
                )
                _fire_overrun_alert_if_crossed(supabase, unit_choice_id)
                del st.session_state["parsed_receipt"]
                del st.session_state["parsed_receipt_raw"]
                st.session_state.pop("suggested_unit_id", None)
                st.session_state.pop("receipt_unit_choice", None)
                st.success("Receipt saved.")
                st.rerun()

    with st.expander("📧 Sync Email Receipts"):
        st.caption(
            "Checks Gmail for unread Home Depot / Lowe's receipt emails, "
            "parses and auto-maps each one, same as the nightly job."
        )
        if st.button("Check Email Now", key="sync_email_receipts", width="stretch"):
            with st.spinner("Connecting to Gmail and checking for receipts..."):
                result = sync_email_receipts(supabase, properties)
            if result["found"] == 0:
                st.info(
                    "No unread receipt emails found — or EMAIL_USER/"
                    "EMAIL_PASSWORD aren't configured in .env yet."
                )
            else:
                st.success(
                    f"Found {result['found']}, processed "
                    f"{result['processed']}, {result['unassigned']} "
                    "unassigned."
                )
                st.rerun()

    try:
        unassigned = (
            supabase.table("material_logs")
            .select("id, store, amount, purchase_date, receipt_details")
            .is_("property_id", "null")
            .order("created_at", desc=True)
            .execute()
            .data
        )
    except Exception:
        unassigned = []

    if unassigned:
        st.divider()
        st.subheader("🗂️ Unassigned Materials")
        for log in unassigned:
            with st.container(border=True, key=f"unassigned_card_{log['id']}"):
                st.markdown(f"**{log['store']}** — ${log['amount']:,.2f}")
                st.caption(log.get("purchase_date") or "")
                if log.get("receipt_details"):
                    st.caption(log["receipt_details"][:200])
                choice = st.selectbox(
                    "Assign to property",
                    ["(unassigned)"] + [p["property_name"] for p in properties],
                    key=f"assign_material_{log['id']}",
                    label_visibility="collapsed",
                )
                if choice != "(unassigned)":
                    target_id = next(
                        p["id"] for p in properties if p["property_name"] == choice
                    )
                    assign_material_log_property(supabase, log["id"], target_id)
                    st.rerun()
