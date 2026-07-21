"""One-time (re-runnable) utility to hydrate start_date / estimated_end_date
for a property's line_items, sequencing tasks in standard construction trade
order from a given baseline start date.

Usage: ./venv/bin/python scripts/reschedule_dates.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import os

from supabase import create_client

from services.scheduler import build_schedule
from utils.units import sort_units_contingency_last

BASELINES = {
    "809 Fred Shuttlesworth Circle": date(2026, 7, 20),
    "811 Fred Shuttlesworth Circle": date(2026, 8, 10),
}


def reschedule_property(client, property_name: str, baseline_start: date) -> dict:
    prop = (
        client.table("properties")
        .select("id, property_name")
        .eq("property_name", property_name)
        .execute()
        .data
    )
    if not prop:
        raise ValueError(f"Property not found: {property_name}")
    property_id = prop[0]["id"]

    units = (
        client.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .order("unit_name")
        .execute()
        .data
    )
    units = sort_units_contingency_last(units)
    unit_ids = [u["id"] for u in units]

    line_items = (
        client.table("line_items")
        .select("id, unit_id, cost_group, budgeted_cost")
        .in_("unit_id", unit_ids)
        .execute()
        .data
    )

    schedule = build_schedule(units, line_items, baseline_start)

    for item_id, dates in schedule.items():
        client.table("line_items").update(dates).eq("id", item_id).execute()

    return {
        "property_name": property_name,
        "baseline_start": baseline_start.isoformat(),
        "line_items_updated": len(schedule),
    }


def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    for property_name, baseline_start in BASELINES.items():
        result = reschedule_property(client, property_name, baseline_start)
        print(result)


if __name__ == "__main__":
    main()
