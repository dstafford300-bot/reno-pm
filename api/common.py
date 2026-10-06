"""Small shared helpers for the routers."""

from datetime import date

from fastapi import HTTPException
from supabase import Client

from utils.status import STATUS_OPTIONS, normalize_status


def get_property(db: Client, property_id: str, columns: str = "*") -> dict:
    rows = db.table("properties").select(columns).eq("id", property_id).execute().data
    if not rows:
        raise HTTPException(404, "Property not found")
    return rows[0]


def assert_writable(prop: dict) -> None:
    if prop.get("archived"):
        raise HTTPException(
            409, "This project is finished and read-only. Reopen it to make changes."
        )


def property_units_and_items(db: Client, property_id: str, item_columns: str):
    units = (
        db.table("units")
        .select("id, unit_name, telegram_chat_id")
        .eq("property_id", property_id)
        .order("unit_name")
        .execute()
        .data
    )
    unit_ids = [u["id"] for u in units]
    items = []
    if unit_ids:
        items = (
            db.table("line_items")
            .select(item_columns)
            .in_("unit_id", unit_ids)
            .execute()
            .data
        )
    return units, items


def parse_iso_date(value: str | None, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(422, f"{field} must be a YYYY-MM-DD date")


def normalized_status(value: str | None) -> str:
    return normalize_status(value)


__all__ = [
    "STATUS_OPTIONS",
    "assert_writable",
    "get_property",
    "normalized_status",
    "parse_iso_date",
    "property_units_and_items",
]
