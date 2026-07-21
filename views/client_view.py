"""Read-only, Gantt-only view for subcontractors (the "Hugo/Tiffany view").

Reached via main.py's routing when ?view=client is present alongside a
valid ?token= — no sidebar, no budget/journal/upload controls, no
edit affordances. Just the live schedule for one property.
"""

import streamlit as st

from db.connection import get_supabase_client
from utils.gantt_chart import prepare_gantt_dataframe, render_responsive_gantt_chart
from utils.units import sort_units_contingency_last


def render(property_id: str | None):
    st.title("📅 Live Project Schedule")

    if not property_id:
        st.warning("This link is missing a property — ask for an updated one.")
        return

    supabase = get_supabase_client()
    prop_rows = (
        supabase.table("properties")
        .select("id, property_name")
        .eq("id", property_id)
        .execute()
        .data
    )
    if not prop_rows:
        st.error("This project link is no longer valid.")
        return

    st.caption(prop_rows[0]["property_name"])

    units = (
        supabase.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .order("unit_name")
        .execute()
        .data
    )
    if not units:
        st.info("No schedule published yet for this property.")
        return
    units = sort_units_contingency_last(units)
    unit_id_to_name = {u["id"]: u["unit_name"] for u in units}

    items = (
        supabase.table("line_items")
        .select(
            "id, unit_id, task_name, cost_group, status, percent_complete, "
            "start_date, estimated_end_date, dependencies"
        )
        .in_("unit_id", list(unit_id_to_name.keys()))
        .execute()
        .data
    )
    if not items:
        st.info("No scheduled tasks yet for this property.")
        return

    df, chronological_order, skipped_count = prepare_gantt_dataframe(
        items, unit_id_to_name=unit_id_to_name, label_with_unit=True
    )
    if skipped_count:
        st.caption(
            f"{skipped_count} task(s) not shown — missing start/end dates."
        )

    render_responsive_gantt_chart(df, chronological_order, key_prefix="client_view")
