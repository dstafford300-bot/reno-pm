import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from api.common import assert_writable, get_property, property_units_and_items, run_parallel
from api.security import (
    any_user,
    assert_property_access,
    owner_only,
    owner_or_pm,
)
from api.task_edits import apply_task_changes, clean_changes, task_label
from db.connection import get_supabase_client
from services.db_writer import apply_schedule_adjustments, log_activity
from services.pm_digest import get_pm_chat_id
from services.telegram_bot import (
    send_and_pin_batch_schedule_update,
    send_and_pin_portal_link,
    send_schedule_adjustment_alert,
    send_telegram_message,
)
from services.timeline_drafter import active_tasks_from_line_items, refine_timeline_draft
from services.trade_nudges import check_and_send_trade_nudges
from utils.status import normalize_status

router = APIRouter(prefix="/api", tags=["schedule"])

_TASK_COLUMNS = (
    "id, unit_id, task_name, cost_group, status, percent_complete, "
    "start_date, estimated_end_date, dependencies"
)


@router.get("/properties/{property_id}/schedule")
def get_schedule(
    property_id: str,
    user: dict = Depends(any_user),
    db: Client = Depends(get_supabase_client),
):
    assert_property_access(db, user, property_id)
    (units, items), prop, pending_requests, pending_publish = run_parallel(
        lambda: property_units_and_items(db, property_id, _TASK_COLUMNS),
        lambda: get_property(db, property_id, "id, property_name, archived"),
        lambda: db.table("change_requests").select("line_item_id")
            .eq("property_id", property_id).eq("status", "pending").execute().data,
        lambda: db.table("pending_schedule_changes").select("id", count="exact")
            .eq("property_id", property_id).execute().count or 0,
    )
    unit_name = {u["id"]: u["unit_name"] for u in units}
    awaiting = {r["line_item_id"] for r in pending_requests}

    tasks = [
        {
            **item,
            "status": normalize_status(item.get("status")),
            "percent_complete": float(item.get("percent_complete") or 0),
            "dependencies": item.get("dependencies") or [],
            "unit_name": unit_name.get(item["unit_id"]),
            "awaiting_approval": item["id"] in awaiting,
        }
        for item in items
    ]
    return {
        "property": {
            "id": prop["id"],
            "property_name": prop["property_name"],
            "archived": bool(prop.get("archived")),
        },
        "units": [{"id": u["id"], "unit_name": u["unit_name"]} for u in units],
        "tasks": tasks,
        "pending_publish_count": pending_publish if user["role"] != "contractor" else 0,
    }


class TaskEdit(BaseModel):
    status: str | None = None
    percent_complete: float | None = None
    start_date: str | None = None
    estimated_end_date: str | None = None
    note: str | None = None


def _load_task(db: Client, task_id: str):
    rows = db.table("line_items").select("*").eq("id", task_id).execute().data
    if not rows:
        raise HTTPException(404, "Task not found")
    item = rows[0]
    unit = db.table("units").select("property_id").eq("id", item["unit_id"]).execute().data
    if not unit:
        raise HTTPException(404, "Task not found")
    return item, unit[0]["property_id"]


@router.patch("/tasks/{task_id}")
def edit_task(
    task_id: str,
    body: TaskEdit,
    user: dict = Depends(any_user),
    db: Client = Depends(get_supabase_client),
):
    item, property_id = _load_task(db, task_id)
    assert_property_access(db, user, property_id)
    prop = get_property(db, property_id, "id, property_name, archived")
    assert_writable(prop)

    changes = clean_changes(
        body.model_dump(exclude={"note"}, exclude_none=True), item
    )

    if user["role"] == "contractor":
        # Proposal only: nothing on the schedule changes until the owner
        # approves it.
        label = task_label(db, item)
        request = (
            db.table("change_requests")
            .insert(
                {
                    "property_id": property_id,
                    "line_item_id": task_id,
                    "requested_by": user["id"],
                    "task_label": label,
                    "changes": changes,
                    "note": (body.note or "").strip() or None,
                }
            )
            .execute()
            .data[0]
        )
        owner_chat = get_pm_chat_id(db)
        if owner_chat:
            threading.Thread(
                target=send_telegram_message,
                args=(
                    owner_chat,
                    f"🎩 {user['name']} has proposed a schedule change at "
                    f"<b>{prop['property_name']}</b>:\n{label}\n\nPlease review it "
                    "in the app's Approvals screen.",
                ),
                daemon=True,
            ).start()
        return {"status": "pending_approval", "request_id": request["id"]}

    apply_task_changes(db, item, changes, property_id, user["id"])
    return {"status": "applied", "changes": changes}


@router.get("/properties/{property_id}/pending-publish")
def pending_publish(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    rows = (
        db.table("pending_schedule_changes")
        .select("id, description, created_at")
        .eq("property_id", property_id)
        .order("created_at")
        .execute()
        .data
    )
    return {"changes": rows}


@router.post("/properties/{property_id}/publish")
def publish_updates(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, telegram_chat_id")
    rows = (
        db.table("pending_schedule_changes")
        .select("id, description")
        .eq("property_id", property_id)
        .order("created_at")
        .execute()
        .data
    )
    if not rows:
        return {"published": 0}
    ok = send_and_pin_batch_schedule_update(
        property_name=prop["property_name"],
        property_id=property_id,
        changes=[r["description"] for r in rows],
        chat_id=prop.get("telegram_chat_id"),
    )
    if not ok:
        raise HTTPException(502, "Couldn't reach Telegram — changes are still pending")
    for r in rows:
        log_activity(db, property_id, "schedule", r["description"])
    db.table("pending_schedule_changes").delete().in_(
        "id", [r["id"] for r in rows]
    ).execute()
    return {"published": len(rows)}


@router.delete("/properties/{property_id}/pending-publish")
def discard_pending_publish(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    db.table("pending_schedule_changes").delete().eq("property_id", property_id).execute()
    return {"ok": True}


# --- AI timeline adjustment --------------------------------------------------
class AdjustPreviewBody(BaseModel):
    instruction: str


class AdjustApplyBody(BaseModel):
    instruction: str
    tasks: list[dict]


@router.post("/properties/{property_id}/schedule/adjust/preview")
def adjust_preview(
    property_id: str,
    body: AdjustPreviewBody,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, archived")
    assert_writable(prop)
    if not body.instruction.strip():
        raise HTTPException(422, "Enter a change first")
    units, items = property_units_and_items(
        db, property_id,
        "id, unit_id, task_name, cost_group, status, start_date, estimated_end_date, dependencies",
    )
    current = active_tasks_from_line_items(
        items, unit_id_to_name={u["id"]: u["unit_name"] for u in units}
    )
    try:
        updated = refine_timeline_draft(prop["property_name"], current, body.instruction)
    except Exception as e:
        raise HTTPException(502, f"Adjustment failed: {e}")
    return {"tasks": updated}


@router.post("/properties/{property_id}/schedule/adjust/apply")
def adjust_apply(
    property_id: str,
    body: AdjustApplyBody,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, archived, telegram_chat_id")
    assert_writable(prop)
    result = apply_schedule_adjustments(db, property_id, body.tasks)
    log_activity(
        db, property_id, "schedule",
        f"AI adjustment: {body.instruction} (updated {result['updated']}, added "
        f"{result['inserted']}, removed {result['deleted']}, renamed {result['renamed']})",
    )
    chat_id = prop.get("telegram_chat_id")
    threading.Thread(
        target=send_schedule_adjustment_alert,
        kwargs=dict(
            property_name=prop["property_name"], property_id=property_id,
            instruction=body.instruction, chat_id=chat_id,
        ),
        daemon=True,
    ).start()
    threading.Thread(
        target=send_and_pin_portal_link,
        kwargs=dict(property_id=property_id, chat_id=chat_id),
        daemon=True,
    ).start()
    return result


@router.post("/properties/{property_id}/nudges/check")
def check_nudges(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    prop = get_property(db, property_id, "id, property_name, telegram_chat_id")
    return check_and_send_trade_nudges(db, [prop])


# --- Contractor change requests (owner approves) -----------------------------
@router.get("/change-requests")
def list_change_requests(
    status: str = "pending",
    user: dict = Depends(any_user),
    db: Client = Depends(get_supabase_client),
):
    query = (
        db.table("change_requests")
        .select("*, app_users!change_requests_requested_by_fkey(name)")
        .eq("status", status)
        .order("created_at", desc=True)
        .limit(200)
    )
    if user["role"] == "contractor":
        query = query.eq("requested_by", user["id"])
    rows = query.execute().data

    props = {
        p["id"]: p["property_name"]
        for p in db.table("properties").select("id, property_name").execute().data
    }
    return [
        {
            "id": r["id"],
            "property_id": r["property_id"],
            "property_name": props.get(r["property_id"]),
            "task_label": r["task_label"],
            "changes": r["changes"],
            "note": r.get("note"),
            "requested_by": (r.get("app_users") or {}).get("name"),
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _decide(db: Client, request_id: str, user: dict, approve: bool) -> dict:
    rows = db.table("change_requests").select("*").eq("id", request_id).execute().data
    if not rows:
        raise HTTPException(404, "Request not found")
    req = rows[0]
    if req["status"] != "pending":
        raise HTTPException(409, f"Already {req['status']}")

    if approve:
        task_rows = db.table("line_items").select("*").eq("id", req["line_item_id"]).execute().data
        if not task_rows:
            raise HTTPException(409, "That task no longer exists")
        prop = get_property(db, req["property_id"], "id, archived")
        assert_writable(prop)
        item = task_rows[0]
        changes = clean_changes(req["changes"], item)
        requester = db.table("app_users").select("name").eq("id", req["requested_by"]).execute().data
        suffix = f" (requested by {requester[0]['name']})" if requester else ""
        apply_task_changes(db, item, changes, req["property_id"], user["id"], suffix)

    db.table("change_requests").update(
        {
            "status": "approved" if approve else "rejected",
            "decided_by": user["id"],
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", request_id).execute()
    return {"status": "approved" if approve else "rejected"}


@router.post("/change-requests/{request_id}/approve")
def approve_request(
    request_id: str,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    return _decide(db, request_id, user, True)


@router.post("/change-requests/{request_id}/reject")
def reject_request(
    request_id: str,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    return _decide(db, request_id, user, False)
