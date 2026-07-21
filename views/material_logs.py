import streamlit as st

from db.connection import get_supabase_client
from utils.mobile import inject_mobile_button_css, inject_mobile_card_css


def render():
    st.title("🧾 Material Logs")
    inject_mobile_button_css()
    inject_mobile_card_css(["material_card_"])

    supabase = get_supabase_client()
    properties = (
        supabase.table("properties")
        .select("id, property_name")
        .order("property_name")
        .execute()
        .data
    )

    if not properties:
        st.info("No properties yet. Upload a SOW to get started.")
        return

    selected_name = st.selectbox("Property", [p["property_name"] for p in properties])
    property_id = next(
        p["id"] for p in properties if p["property_name"] == selected_name
    )

    try:
        logs = (
            supabase.table("material_logs")
            .select(
                "id, store, amount, purchase_date, receipt_details, photo_url, "
                "source, line_items_json"
            )
            .eq("property_id", property_id)
            .order("purchase_date", desc=True)
            .execute()
            .data
        )
    except Exception:
        st.error(
            "The `material_logs` table doesn't have the expected columns "
            "yet — run scripts/migration_material_logs.sql via Supabase's "
            "SQL Editor, then refresh."
        )
        return

    total_spent = sum(log.get("amount") or 0 for log in logs)
    st.metric("Total Materials Logged", f"${total_spent:,.2f}")
    st.divider()

    if not logs:
        st.info(
            "No material purchases logged yet for this property. Receipts "
            "sent to Jeeves in Telegram (photo + \"Jeeves receipt\") or "
            "pasted on the Budget page will show up here."
        )
        return

    for log in logs:
        with st.container(border=True, key=f"material_card_{log['id']}"):
            col_info, col_photo = st.columns([3, 1])
            with col_info:
                st.markdown(f"**{log['store']}** — ${log['amount']:,.2f}")
                st.caption(f"{log.get('purchase_date') or ''} · via {log.get('source')}")
                if log.get("receipt_details"):
                    st.caption(log["receipt_details"][:300])
                for li in log.get("line_items_json") or []:
                    st.write(f"• {li.get('description')} — ${li.get('cost', 0):,.2f}")
            with col_photo:
                if log.get("photo_url"):
                    st.image(log["photo_url"])
