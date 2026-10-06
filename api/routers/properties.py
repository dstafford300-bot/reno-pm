import re
import threading
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from api.common import assert_writable, get_property, property_units_and_items
from api.security import (
    accessible_property_ids,
    any_user,
    assert_property_access,
    can_see_costs,
    owner_only,
    owner_or_pm,
)
from db.connection import get_supabase_client
from services.db_writer import (
    clear_property_telegram_chat_id,
    delete_property_cascade,
    log_activity,
    publish_draft_timeline,
    set_property_archived,
)
from services.pm_digest import clear_pm_chat_id, get_pm_chat_id, send_daily_pm_digest
from services.telegram_bot import (
    PM_DIGEST_VERIFICATION_PHRASE,
    send_and_pin_portal_link,
    send_timeline_published_alert,
    verification_phrase,
)
from services.timeline_drafter import draft_timeline_from_notes, refine_timeline_draft
from utils.status import normalize_status

router = APIRouter(prefix="/api", tags=["properties"])

# "Unit 2", "Apt 3", "Suite 1" — not "Rear Staircase — Units 2 & 3".
_LIVING_UNIT = re.compile(r"\bunit\s+\w|\bunit\d|\b(apt|apartment|suite|floor)\b", re.I)


def _progress(items: list[dict]) -> float:
    if not items:
        return 0.0
    done = sum(1 for i in items if normalize_status(i.get("status")) == "Completed")
    return round(done / len(items) * 100, 1)


@router.get("/properties")
def list_properties(
    user: dict = Depends(any_user), db: Client = Depends(get_supabase_client)
):
    allowed = accessible_property_ids(db, user)
    props = (
        db.table("properties")
        .select("id, property_name, address, archived")
        .order("property_name")
        .execute()
        .data
    )
    if allowed is not None:
        props = [p for p in props if p["id"] in allowed]

    # One query for every task across all properties (joined to its unit's
    # property) rather than units + tasks per property.
    ids = [p["id"] for p in props]
    all_items = (
        db.table("line_items")
        .select("id, status, budgeted_cost, units!inner(property_id)")
        .in_("units.property_id", ids)
        .execute()
        .data
        if ids else []
    )
    items_by_property: dict[str, list] = {}
    for item in all_items:
        items_by_property.setdefault(item["units"]["property_id"], []).append(item)

    out = []
    for p in props:
        items = items_by_property.get(p["id"], [])
        row = {
            "id": p["id"],
            "property_name": p["property_name"],
            "address": p.get("address"),
            "archived": bool(p.get("archived")),
            "progress_percent": _progress(items),
            "task_count": len(items),
        }
        if can_see_costs(user):
            row["total_budget"] = sum(i.get("budgeted_cost") or 0 for i in items)
        out.append(row)
    return out


@router.get("/properties/{property_id}")
def get_property_detail(
    property_id: str,
    user: dict = Depends(any_user),
    db: Client = Depends(get_supabase_client),
):
    assert_property_access(db, user, property_id)
    prop = get_property(db, property_id)
    columns = "id, unit_id, task_name, cost_group, status, percent_complete"
    if can_see_costs(user):
        columns += ", budgeted_cost"
    units, items = property_units_and_items(db, property_id, columns)

    by_unit: dict[str, list] = {}
    for item in items:
        item["status"] = normalize_status(item.get("status"))
        by_unit.setdefault(item["unit_id"], []).append(item)

    detail = {
        "id": prop["id"],
        "property_name": prop["property_name"],
        "address": prop.get("address"),
        "archived": bool(prop.get("archived")),
        "progress_percent": _progress(items),
        "living_units": sum(1 for u in units if _LIVING_UNIT.search(u["unit_name"])),
        "has_schedule": bool(items),
        "units": [
            {"id": u["id"], "unit_name": u["unit_name"], "tasks": by_unit.get(u["id"], [])}
            for u in units
        ],
    }
    if can_see_costs(user):
        detail["total_budget"] = sum(i.get("budgeted_cost") or 0 for i in items)
    if user["role"] in ("owner", "pm"):
        detail["telegram_linked"] = bool(prop.get("telegram_chat_id"))
        detail["telegram_phrase"] = verification_phrase(prop["property_name"])
    return detail


class ArchiveBody(BaseModel):
    archived: bool


@router.post("/properties/{property_id}/archive")
def archive_property(
    property_id: str,
    body: ArchiveBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    get_property(db, property_id, "id")
    set_property_archived(db, property_id, body.archived)
    return {"archived": body.archived}


class DeleteBody(BaseModel):
    confirm_name: str


@router.delete("/properties/{property_id}")
def delete_property(
    property_id: str,
    body: DeleteBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name")
    if body.confirm_name != prop["property_name"]:
        raise HTTPException(422, "Type the property name exactly to confirm")
    delete_property_cascade(db, property_id)
    return {"deleted": True}


@router.post("/properties/{property_id}/telegram/refresh-link")
def refresh_portal_link(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, telegram_chat_id")
    if not prop.get("telegram_chat_id"):
        raise HTTPException(409, "No Telegram group is linked to this property yet")
    ok = send_and_pin_portal_link(property_id, prop["telegram_chat_id"])
    if not ok:
        raise HTTPException(502, "Telegram didn't accept the message")
    return {"ok": True}


@router.post("/properties/{property_id}/telegram/unlink")
def unlink_telegram(
    property_id: str,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    get_property(db, property_id, "id")
    clear_property_telegram_chat_id(db, property_id)
    return {"ok": True}


# --- PM daily digest DM link --------------------------------------------
@router.get("/pm-digest")
def pm_digest_status(
    user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)
):
    return {"linked": bool(get_pm_chat_id(db)), "phrase": PM_DIGEST_VERIFICATION_PHRASE}


@router.post("/pm-digest/test")
def pm_digest_test(
    user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)
):
    if not send_daily_pm_digest(db):
        raise HTTPException(409, "Link your Telegram chat first, or Telegram refused it")
    return {"ok": True}


@router.delete("/pm-digest")
def pm_digest_unlink(
    user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)
):
    clear_pm_chat_id(db)
    return {"ok": True}


# --- Timeline builder (a property with no schedule yet) -------------------
class DraftBody(BaseModel):
    notes: str


class RefineBody(BaseModel):
    tasks: list[dict]
    instruction: str


class PublishBody(BaseModel):
    tasks: list[dict]


@router.post("/properties/{property_id}/timeline/draft")
def timeline_draft(
    property_id: str,
    body: DraftBody,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, archived")
    assert_writable(prop)
    if not body.notes.strip():
        raise HTTPException(422, "Add some notes first")
    try:
        tasks = draft_timeline_from_notes(prop["property_name"], body.notes, date.today())
    except Exception as e:
        raise HTTPException(502, f"Draft generation failed: {e}")
    return {"tasks": tasks}


@router.post("/properties/{property_id}/timeline/refine")
def timeline_refine(
    property_id: str,
    body: RefineBody,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, archived")
    assert_writable(prop)
    try:
        tasks = refine_timeline_draft(prop["property_name"], body.tasks, body.instruction)
    except Exception as e:
        raise HTTPException(502, f"Refinement failed: {e}")
    return {"tasks": tasks}


@router.post("/properties/{property_id}/timeline/publish")
def timeline_publish(
    property_id: str,
    body: PublishBody,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, archived, telegram_chat_id")
    assert_writable(prop)
    summary = publish_draft_timeline(db, property_id, "General Scope", body.tasks)
    log_activity(db, property_id, "schedule", f"Timeline published ({summary['line_items']} tasks)")
    chat_id = prop.get("telegram_chat_id")
    threading.Thread(
        target=send_timeline_published_alert,
        kwargs=dict(
            property_name=prop["property_name"], property_id=property_id,
            tasks=body.tasks, chat_id=chat_id,
        ),
        daemon=True,
    ).start()
    threading.Thread(
        target=send_and_pin_portal_link,
        kwargs=dict(property_id=property_id, chat_id=chat_id),
        daemon=True,
    ).start()
    return summary
