import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from api.common import assert_writable, get_property, property_units_and_items, run_parallel
from api.security import assert_property_access, owner_only, owner_or_pm
from db.connection import get_supabase_client
from services.db_writer import (
    assign_material_log_property,
    assign_material_log_unit,
    check_unit_overrun,
    create_draw_milestone,
    create_material_log,
    delete_draw_milestone,
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

router = APIRouter(prefix="/api", tags=["budget"])


def _fire_overrun_alert(db: Client, unit_id: str | None) -> None:
    """Best-effort Telegram warning if filing a purchase under a unit pushed
    its labor+materials budget past the threshold. Never blocks the caller."""
    if not unit_id:
        return
    try:
        overrun = check_unit_overrun(db, unit_id)
    except Exception:
        return
    if overrun:
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


def _spend_by_unit(units, items, logs):
    """(per-unit spend rows, unassigned total) from rows already fetched —
    a purchase belongs to its own unit_id, or (older rows) its task's unit."""
    task_unit = {i["id"]: i["unit_id"] for i in items}
    name = {u["id"]: u["unit_name"] for u in units}
    by_unit: dict[str, dict] = {}
    for log in logs:
        unit = log.get("unit_id") or task_unit.get(log.get("line_item_id"))
        if unit:
            e = by_unit.setdefault(unit, {"spent": 0.0, "count": 0})
            e["spent"] += float(log.get("amount") or 0)
            e["count"] += 1
    budget: dict[str, float] = {}
    for i in items:
        if i.get("budget_includes_materials"):
            budget[i["unit_id"]] = budget.get(i["unit_id"], 0) + (i.get("budgeted_cost") or 0)
    rows = []
    for unit_id, e in by_unit.items():
        b = budget.get(unit_id)
        rows.append({
            "unit_id": unit_id, "unit_name": name.get(unit_id, "Unknown unit"),
            "spent": e["spent"], "count": e["count"],
            "labor_plus_materials_budget": b if b else None,
            "variance": (b - e["spent"]) if b else None,
        })
    rows.sort(key=lambda r: r["spent"], reverse=True)
    return rows


def _milestone_progress(db: Client, milestones: list[dict], items: list[dict]) -> None:
    """Fills task_progress/eligible on every milestone with ONE query for all
    their task requirements (the per-milestone version made ~2 queries each)."""
    if not milestones:
        return
    reqs = (
        db.table("draw_milestone_tasks")
        .select("milestone_id, line_item_id, required_percent")
        .in_("milestone_id", [m["id"] for m in milestones])
        .execute()
        .data
    )
    by_item = {i["id"]: i for i in items}
    for m in milestones:
        progress = []
        for r in reqs:
            item = by_item.get(r["line_item_id"])
            if r["milestone_id"] == m["id"] and item:
                progress.append({
                    "line_item_id": r["line_item_id"], "task_name": item["task_name"],
                    "required_percent": float(r["required_percent"]),
                    "actual_percent": float(item.get("percent_complete") or 0),
                })
        m["task_progress"] = progress
        m["eligible"] = milestone_is_eligible(progress)
        m["draw_amount"] = float(m["draw_amount"] or 0)


@router.get("/properties/{property_id}/budget")
def get_budget(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    assert_property_access(db, user, property_id)

    def fetch_units_items():
        return property_units_and_items(
            db, property_id,
            "id, unit_id, task_name, budgeted_cost, budget_includes_materials, percent_complete",
        )

    (units, items), prop, milestones, logs = run_parallel(
        fetch_units_items,
        lambda: get_property(db, property_id, "id, property_name, archived"),
        lambda: db.table("draw_milestones")
            .select("id, milestone_name, draw_amount, status, released_at")
            .eq("property_id", property_id).order("created_at").execute().data,
        lambda: db.table("material_logs")
            .select("id, store, amount, purchase_date, unit_id, line_item_id")
            .eq("property_id", property_id).order("purchase_date", desc=True).execute().data,
    )
    unit_name = {u["id"]: u["unit_name"] for u in units}
    _milestone_progress(db, milestones, items)

    released = sum(m["draw_amount"] for m in milestones if m["status"] == "Released")
    pending = [m for m in milestones if m["status"] != "Released"]
    needs_unit = [
        {
            "id": l["id"], "store": l["store"], "amount": float(l["amount"] or 0),
            "purchase_date": (l.get("purchase_date") or "")[:10],
        }
        for l in logs
        if not l.get("unit_id") and not l.get("line_item_id")
    ]

    return {
        "property": {
            "id": prop["id"], "property_name": prop["property_name"],
            "archived": bool(prop.get("archived")),
        },
        "total_budget": sum(i.get("budgeted_cost") or 0 for i in items),
        "materials_logged": sum(float(l["amount"] or 0) for l in logs),
        "total_released": released,
        "next_draw": (
            {"amount": pending[0]["draw_amount"], "name": pending[0]["milestone_name"]}
            if pending else None
        ),
        "milestones": milestones,
        "materials_by_unit": _spend_by_unit(units, items, logs),
        "needs_unit": needs_unit,
        "units": [{"id": u["id"], "unit_name": u["unit_name"]} for u in units],
        "tasks": [
            {
                "id": i["id"],
                "label": f"{unit_name.get(i['unit_id'])}: {i['task_name']}",
                "budget_includes_materials": bool(i.get("budget_includes_materials")),
            }
            for i in items
        ],
    }


# --- draws -------------------------------------------------------------------
class TaskRequirement(BaseModel):
    line_item_id: str
    required_percent: float


class MilestoneBody(BaseModel):
    milestone_name: str
    draw_amount: float
    task_requirements: list[TaskRequirement] = []


@router.post("/properties/{property_id}/milestones")
def add_milestone(
    property_id: str,
    body: MilestoneBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    assert_writable(get_property(db, property_id, "id, archived"))
    if not body.milestone_name.strip():
        raise HTTPException(422, "Enter a milestone name")
    if body.draw_amount < 0:
        raise HTTPException(422, "Draw amount can't be negative")
    milestone = create_draw_milestone(
        db, property_id, body.milestone_name.strip(), body.draw_amount,
        [r.model_dump() for r in body.task_requirements],
    )
    return {"id": milestone["id"]}


@router.delete("/milestones/{milestone_id}")
def remove_milestone(
    milestone_id: str,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    rows = db.table("draw_milestones").select("property_id").eq("id", milestone_id).execute().data
    if not rows:
        raise HTTPException(404, "Milestone not found")
    assert_writable(get_property(db, rows[0]["property_id"], "id, archived"))
    delete_draw_milestone(db, milestone_id)
    return {"ok": True}


class ReleaseBody(BaseModel):
    confirm_not_eligible: bool = False


@router.post("/milestones/{milestone_id}/release")
def release_milestone(
    milestone_id: str,
    body: ReleaseBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    rows = (
        db.table("draw_milestones")
        .select("id, property_id, milestone_name, draw_amount, status")
        .eq("id", milestone_id)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(404, "Milestone not found")
    m = rows[0]
    if m["status"] == "Released":
        raise HTTPException(409, "Already released")
    prop = get_property(db, m["property_id"], "id, property_name, archived, telegram_chat_id")
    assert_writable(prop)

    if not milestone_is_eligible(get_milestone_task_progress(db, milestone_id)) \
            and not body.confirm_not_eligible:
        raise HTTPException(
            409,
            "Not every linked task has met its required completion yet. "
            "Confirm to release anyway.",
        )
    release_draw_milestone(db, milestone_id)
    log_activity(
        db, m["property_id"], "draw",
        f"{m['milestone_name']} released — ${float(m['draw_amount']):,.2f}",
    )
    threading.Thread(
        target=send_draw_release_alert,
        kwargs=dict(
            property_name=prop["property_name"],
            milestone_name=m["milestone_name"],
            draw_amount=float(m["draw_amount"]),
            chat_id=prop.get("telegram_chat_id"),
        ),
        daemon=True,
    ).start()
    return {"released": True}


# --- materials ---------------------------------------------------------------
class UnitBody(BaseModel):
    unit_id: str | None


@router.post("/material-logs/{log_id}/unit")
def set_log_unit(
    log_id: str,
    body: UnitBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    rows = db.table("material_logs").select("id, property_id").eq("id", log_id).execute().data
    if not rows:
        raise HTTPException(404, "Purchase not found")
    if rows[0].get("property_id"):
        assert_writable(get_property(db, rows[0]["property_id"], "id, archived"))
    if body.unit_id:
        unit = db.table("units").select("property_id").eq("id", body.unit_id).execute().data
        if not unit or unit[0]["property_id"] != rows[0].get("property_id"):
            raise HTTPException(422, "That unit isn't part of this purchase's property")
    assign_material_log_unit(db, log_id, body.unit_id)
    _fire_overrun_alert(db, body.unit_id)
    return {"ok": True}


class PropertyBody(BaseModel):
    property_id: str


@router.get("/materials/unassigned")
def unassigned_materials(
    user: dict = Depends(owner_or_pm), db: Client = Depends(get_supabase_client)
):
    rows = (
        db.table("material_logs")
        .select("id, store, amount, purchase_date, receipt_details")
        .is_("property_id", "null")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return [
        {
            "id": r["id"], "store": r["store"], "amount": float(r["amount"] or 0),
            "purchase_date": (r.get("purchase_date") or "")[:10],
            "snippet": (r.get("receipt_details") or "")[:200],
        }
        for r in rows
    ]


@router.post("/material-logs/{log_id}/property")
def set_log_property(
    log_id: str,
    body: PropertyBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    assert_writable(get_property(db, body.property_id, "id, archived"))
    assign_material_log_property(db, log_id, body.property_id)
    return {"ok": True}


class FlagsBody(BaseModel):
    line_item_ids: list[str]


@router.put("/properties/{property_id}/budget/materials-flags")
def set_materials_flags(
    property_id: str,
    body: FlagsBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    assert_writable(get_property(db, property_id, "id, archived"))
    _, items = property_units_and_items(db, property_id, "id")
    all_ids = {i["id"] for i in items}
    chosen = [i for i in body.line_item_ids if i in all_ids]
    set_budget_includes_materials(db, chosen, True)
    set_budget_includes_materials(db, [i for i in all_ids if i not in set(chosen)], False)
    return {"flagged": len(chosen)}


@router.get("/properties/{property_id}/material-logs")
def property_material_logs(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    assert_property_access(db, user, property_id)
    get_property(db, property_id, "id")
    units, items = property_units_and_items(db, property_id, "id, unit_id")
    unit_name = {u["id"]: u["unit_name"] for u in units}
    task_unit = {i["id"]: i["unit_id"] for i in items}
    logs = (
        db.table("material_logs")
        .select("id, store, amount, purchase_date, receipt_details, photo_url, "
                "source, line_items_json, unit_id, line_item_id")
        .eq("property_id", property_id)
        .order("purchase_date", desc=True)
        .execute()
        .data
    )
    out = []
    for l in logs:
        unit_id = l.get("unit_id") or task_unit.get(l.get("line_item_id"))
        out.append({
            "id": l["id"], "store": l["store"], "amount": float(l["amount"] or 0),
            "purchase_date": (l.get("purchase_date") or "")[:10],
            "source": l.get("source"),
            "unit_name": unit_name.get(unit_id),
            "photo_url": l.get("photo_url"),
            "items": l.get("line_items_json") or [],
            "details": (l.get("receipt_details") or "")[:400],
        })
    return {"logs": out, "total": sum(o["amount"] for o in out)}


# --- receipts ----------------------------------------------------------------
class ParseBody(BaseModel):
    text: str


@router.post("/receipts/parse")
def parse_receipt(
    body: ParseBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    if not body.text.strip():
        raise HTTPException(422, "Paste a receipt first")
    try:
        parsed = parse_receipt_text(body.text)
    except Exception as e:
        raise HTTPException(422, f"Couldn't read that as a receipt: {e}")

    properties = db.table("properties").select("id, property_name").execute().data
    property_id = match_property_from_text(body.text, properties)
    units, suggested = [], None
    if property_id:
        units = (
            db.table("units").select("id, unit_name").eq("property_id", property_id)
            .execute().data
        )
        try:
            suggested = match_unit_from_reference(
                parsed.get("job_or_property_reference") or body.text, units
            )
        except Exception:
            suggested = None
    return {
        "parsed": parsed,
        "property_id": property_id,
        "units": units,
        "suggested_unit_id": suggested,
        "raw_text": body.text,
    }


class SaveReceiptBody(BaseModel):
    store_name: str
    total_cost: float
    purchase_date: str | None = None
    line_items: list[dict] = []
    property_id: str | None = None
    unit_id: str | None = None
    raw_text: str | None = None


@router.post("/receipts")
def save_receipt(
    body: SaveReceiptBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    if body.property_id:
        assert_writable(get_property(db, body.property_id, "id, archived"))
    create_material_log(
        db,
        store=body.store_name,
        amount=body.total_cost,
        property_id=body.property_id,
        purchase_date=body.purchase_date,
        receipt_details=body.raw_text,
        source="manual",
        line_items_json=body.line_items,
        unit_id=body.unit_id,
    )
    _fire_overrun_alert(db, body.unit_id)
    return {"ok": True}


@router.post("/receipts/sync-email")
def sync_email(
    user: dict = Depends(owner_only), db: Client = Depends(get_supabase_client)
):
    properties = (
        db.table("properties").select("id, property_name, telegram_chat_id").execute().data
    )
    return sync_email_receipts(db, properties)
