"""Read-only schedule for a shared link — keeps the existing Telegram portal
links (?token=…&view=client&property_id=…) and the demo link working, with
no login. Never includes costs."""

import hmac

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from api.common import get_property, property_units_and_items
from db.connection import get_supabase_client
from services.demo_access import get_demo_token
from utils.settings import get_setting
from utils.status import normalize_status

router = APIRouter(prefix="/api/public", tags=["public"])


def _token_ok(db: Client, token: str) -> bool:
    candidates = [get_setting("ACCESS_TOKEN")]
    try:
        candidates.append(get_demo_token(db))
    except Exception:
        pass
    return any(c and hmac.compare_digest(token, c) for c in candidates)


@router.get("/schedule")
def public_schedule(
    token: str, property_id: str, db: Client = Depends(get_supabase_client)
):
    if not _token_ok(db, token):
        raise HTTPException(401, "This link isn't valid")
    prop = get_property(db, property_id, "id, property_name")
    units, items = property_units_and_items(
        db, property_id,
        "id, unit_id, task_name, cost_group, status, percent_complete, "
        "start_date, estimated_end_date, dependencies",
    )
    unit_name = {u["id"]: u["unit_name"] for u in units}
    return {
        "property": {"id": prop["id"], "property_name": prop["property_name"]},
        "units": [{"id": u["id"], "unit_name": u["unit_name"]} for u in units],
        "tasks": [
            {
                **i,
                "status": normalize_status(i.get("status")),
                "percent_complete": float(i.get("percent_complete") or 0),
                "dependencies": i.get("dependencies") or [],
                "unit_name": unit_name.get(i["unit_id"]),
                "awaiting_approval": False,
            }
            for i in items
        ],
    }
