import threading

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import (
    assign_material_log_property,
    create_draw_milestone,
    create_material_log,
    delete_draw_milestone,
    get_milestone_task_progress,
    log_activity,
    milestone_is_eligible,
    release_draw_milestone,
)
from services.email_receipts import sync_email_receipts
from services.receipt_parser import match_property_from_text, parse_receipt_text
from services.telegram_bot import send_draw_release_alert
from utils.mobile import inject_mobile_button_css, inject_mobile_card_css


def render():
    st.title("💰 Budget Draw Control")
    inject_mobile_button_css()
    inject_mobile_card_css(["milestone_card_", "unassigned_card_"])

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

    selected_name = st.selectbox("Property", [p["property_name"] for p in properties])
    selected_property = next(
        p for p in properties if p["property_name"] == selected_name
    )
    property_id = selected_property["id"]

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
            .select("id, task_name, budgeted_cost")
            .in_("unit_id", unit_ids)
            .execute()
            .data
        )

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
                    "🗑️ Delete Milestone", key=f"delete_{m['id']}", width="stretch"
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

        if st.button("Add Milestone", type="primary", width="stretch"):
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
                )
                del st.session_state["parsed_receipt"]
                del st.session_state["parsed_receipt_raw"]
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
