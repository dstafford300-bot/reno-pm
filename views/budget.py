import threading

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import (
    assign_material_log_line_item,
    assign_material_log_property,
    check_line_item_overrun,
    create_draw_milestone,
    create_material_log,
    delete_draw_milestone,
    get_line_item_cost_variance,
    get_line_items_with_labels,
    get_milestone_task_progress,
    log_activity,
    milestone_is_eligible,
    release_draw_milestone,
)
from services.email_receipts import sync_email_receipts
from services.receipt_parser import (
    match_line_item_from_receipt,
    match_property_from_text,
    parse_receipt_text,
)
from services.telegram_bot import send_cost_overrun_alert, send_draw_release_alert
from utils.mobile import inject_mobile_button_css, inject_mobile_card_css


def _fire_overrun_alert_if_crossed(supabase, line_item_id: str | None) -> None:
    """Best-effort: checks whether assigning this purchase just pushed the
    task's spend over the alert threshold, and if so fires the Telegram
    alert in the background. No-ops silently on any failure — a missed
    overrun alert shouldn't block saving/assigning a purchase."""
    if not line_item_id:
        return
    try:
        overrun = check_line_item_overrun(supabase, line_item_id)
    except Exception:
        return
    if not overrun:
        return
    threading.Thread(
        target=send_cost_overrun_alert,
        kwargs=dict(
            property_name=overrun["property_name"],
            unit_name=overrun["unit_name"],
            task_name=overrun["task_name"],
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

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Budgeted SOW Cost", f"${total_budgeted:,.0f}")
    col2.metric("Total Funds Released", f"${total_released:,.0f}")
    col3.metric(
        "Next Upcoming Draw",
        f"${next_milestone['draw_amount']:,.0f}" if next_milestone else "—",
        help=next_milestone["milestone_name"] if next_milestone else None,
    )

    st.divider()

    st.subheader("📊 Cost Variance by Task")
    st.caption(
        "Compares each task's budgeted cost against material purchases "
        "logged against it. Tasks with no purchases assigned yet aren't "
        "shown — assign purchases to tasks below or from the receipt "
        "import flow."
    )
    variance_migration_missing = False
    try:
        variance_rows = get_line_item_cost_variance(supabase, property_id)
    except Exception:
        variance_rows = []
        variance_migration_missing = True
        st.caption(
            "Run scripts/migration_material_line_item.sql via Supabase's "
            "SQL Editor to enable task-level cost tracking."
        )
    if variance_rows:
        for row in variance_rows:
            over = row["variance"] < 0
            label = f"**{row['unit_name']}: {row['task_name']}**"
            cols = st.columns([3, 1, 1, 1])
            cols[0].markdown(label)
            cols[1].caption(f"Budgeted ${row['budgeted_cost']:,.0f}")
            cols[2].caption(f"Spent ${row['spent']:,.0f}")
            if over:
                cols[3].error(f"−${abs(row['variance']):,.0f}")
            else:
                cols[3].success(f"+${row['variance']:,.0f}")
    elif not variance_migration_missing:
        st.caption("No purchases have been assigned to a task yet.")

    if not variance_migration_missing:
        try:
            untasked_logs = (
                supabase.table("material_logs")
                .select("id, store, amount, purchase_date, receipt_details")
                .eq("property_id", property_id)
                .is_("line_item_id", "null")
                .order("purchase_date", desc=True)
                .execute()
                .data
            )
        except Exception:
            untasked_logs = []
        if untasked_logs and line_item_labels:
            with st.expander(
                f"🧮 Assign purchases to tasks ({len(untasked_logs)} unassigned)"
            ):
                for log in untasked_logs:
                    st.markdown(f"**{log['store']}** — ${log['amount']:,.2f}")
                    st.caption(log.get("purchase_date") or "")
                    task_choice = st.selectbox(
                        "Assign to task",
                        ["(unassigned)"] + [row["label"] for row in line_item_labels],
                        key=f"assign_task_{log['id']}",
                        label_visibility="collapsed",
                        disabled=is_archived,
                    )
                    if task_choice != "(unassigned)":
                        chosen_id = next(
                            row["id"]
                            for row in line_item_labels
                            if row["label"] == task_choice
                        )
                        assign_material_log_line_item(supabase, log["id"], chosen_id)
                        _fire_overrun_alert_if_crossed(supabase, chosen_id)
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

            task_choice_id = None
            if matched_id:
                candidate_labels = (
                    line_item_labels
                    if matched_id == property_id
                    else get_line_items_with_labels(supabase, matched_id)
                )
                if candidate_labels and "suggested_task_id" not in st.session_state:
                    with st.spinner("Checking which task this was for..."):
                        st.session_state["suggested_task_id"] = (
                            match_line_item_from_receipt(
                                st.session_state.get("parsed_receipt_raw", ""),
                                candidate_labels,
                            )
                        )
                label_options = ["(none — property-level only)"] + [
                    row["label"] for row in candidate_labels
                ]
                suggested_id = st.session_state.get("suggested_task_id")
                suggested_label = next(
                    (
                        row["label"]
                        for row in candidate_labels
                        if row["id"] == suggested_id
                    ),
                    None,
                )
                default_index = (
                    label_options.index(suggested_label) if suggested_label else 0
                )
                if suggested_label:
                    st.info(f"Suggested task: {suggested_label}")
                task_label_choice = st.selectbox(
                    "Assign to task (optional)",
                    label_options,
                    index=default_index,
                    key="receipt_task_choice",
                )
                if task_label_choice != "(none — property-level only)":
                    task_choice_id = next(
                        row["id"]
                        for row in candidate_labels
                        if row["label"] == task_label_choice
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
                    line_item_id=task_choice_id,
                )
                _fire_overrun_alert_if_crossed(supabase, task_choice_id)
                del st.session_state["parsed_receipt"]
                del st.session_state["parsed_receipt_raw"]
                st.session_state.pop("suggested_task_id", None)
                st.session_state.pop("receipt_task_choice", None)
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
