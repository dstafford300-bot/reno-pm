"""Validating and applying edits to a task (line item).

Used by direct edits (owner/pm) and by the owner approving a contractor's
change request, so both follow identical rules.
"""

from datetime import date

from fastapi import HTTPException
from supabase import Client

from api.common import parse_iso_date
from utils.status import STATUS_OPTIONS, normalize_status


_KNOWN_STATUSES = {"pending", "in progress", "completed", "complete"}


def clean_changes(raw: dict, current: dict) -> dict:
    """Returns the DB columns to write for `raw` (status / percent_complete /
    start_date / estimated_end_date), validated against the task's current
    values. Rules:
      - An explicit status wins; percent then defaults from it
        (Completed -> 100, Pending -> 0, In Progress -> given/existing).
      - With only a percent, status follows it (100 Completed, 0 Pending,
        otherwise In Progress) — so it can never disagree.
      - End date can't precede start date.
    """
    out: dict = {}

    status = raw.get("status")
    percent = raw.get("percent_complete")
    if status is not None:
        if str(status).strip().lower() not in _KNOWN_STATUSES:
            raise HTTPException(422, f"status must be one of {STATUS_OPTIONS}")
        status = normalize_status(status)
    if percent is not None:
        try:
            percent = float(percent)
        except (TypeError, ValueError):
            raise HTTPException(422, "percent_complete must be a number")
        if not 0 <= percent <= 100:
            raise HTTPException(422, "percent_complete must be between 0 and 100")

    if status == "Completed":
        percent = 100.0
    elif status == "Pending":
        percent = 0.0
    elif status == "In Progress" and percent is None:
        existing = float(current.get("percent_complete") or 0)
        percent = existing if 0 < existing < 100 else 0.0
    elif status is None and percent is not None:
        status = (
            "Completed" if percent == 100 else "Pending" if percent == 0 else "In Progress"
        )

    if status is not None:
        out["status"] = status
    if percent is not None:
        out["percent_complete"] = percent

    start = parse_iso_date(raw.get("start_date"), "start_date")
    end = parse_iso_date(raw.get("estimated_end_date"), "estimated_end_date")
    cur_start = parse_iso_date(current.get("start_date"), "start_date")
    cur_end = parse_iso_date(current.get("estimated_end_date"), "estimated_end_date")
    final_start = start or cur_start
    final_end = end or cur_end
    if final_start and final_end and final_end < final_start:
        raise HTTPException(422, "End date must be on or after the start date")
    if start:
        out["start_date"] = start.isoformat()
    if end:
        out["estimated_end_date"] = end.isoformat()

    if not out:
        raise HTTPException(422, "Nothing to change")
    return out


def describe_change(label: str, current: dict, changes: dict) -> str:
    """Human description of what changed, for the Telegram publish message,
    e.g. "Unit 3: Paint — now 40% complete, extended 3 days, new ETC 10-14-2026"."""
    parts = []

    new_end = changes.get("estimated_end_date")
    new_start = changes.get("start_date")
    cur_end = current.get("estimated_end_date")
    cur_start = current.get("start_date")
    if (new_end and new_end != cur_end) or (new_start and new_start != cur_start):
        if new_end and cur_end and new_end != cur_end:
            delta = (date.fromisoformat(new_end) - date.fromisoformat(cur_end)).days
            shown = date.fromisoformat(new_end).strftime("%m-%d-%Y")
            word = "day" if abs(delta) == 1 else "days"
            if delta > 0:
                parts.append(f"extended {delta} {word}, new ETC {shown}")
            elif delta < 0:
                parts.append(f"pulled in {abs(delta)} {word}, new ETC {shown}")
        elif new_start:
            parts.append(f"start moved to {date.fromisoformat(new_start).strftime('%m-%d-%Y')}")

    status = changes.get("status")
    percent = changes.get("percent_complete")
    if status and status != normalize_status(current.get("status")):
        parts.append(
            f"now {status}" + (f" ({percent:.0f}%)" if status == "In Progress" and percent else "")
        )
    elif percent is not None and percent != float(current.get("percent_complete") or 0):
        parts.append(f"now {percent:.0f}% complete")

    return f"{label}: {', '.join(parts)}" if parts else ""


def task_label(db: Client, item: dict) -> str:
    units = db.table("units").select("unit_name").eq("id", item["unit_id"]).execute().data
    unit = units[0]["unit_name"] if units else None
    return f"{unit}: {item['task_name']}" if unit else item["task_name"]


def apply_task_changes(
    db: Client, item: dict, changes: dict, property_id: str,
    created_by: str | None, suffix: str = "",
) -> str:
    """Writes `changes` (already cleaned) to the task and queues a line for
    the next Telegram "publish updates" message. Returns that description."""
    db.table("line_items").update(changes).eq("id", item["id"]).execute()
    description = describe_change(task_label(db, item), item, changes)
    if description:
        db.table("pending_schedule_changes").insert(
            {
                "property_id": property_id,
                "description": description + suffix,
                "created_by": created_by,
            }
        ).execute()
    return description
