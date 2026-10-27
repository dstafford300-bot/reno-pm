from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from supabase import Client

from api.common import assert_writable, get_property, property_units_and_items
from api.security import assert_property_access, owner_or_pm
from db.connection import get_supabase_client
from services.storage import JOURNAL_MEDIA_BUCKET, signed_urls
from services.telegram_bot import download_file_bytes

router = APIRouter(prefix="/api", tags=["journal"])

_PHOTO_BURST_SECONDS = 600


def _is_photo_only(e: dict) -> bool:
    has_photo = e.get("photo_file_id") or (e.get("media_kind") == "photo" and e.get("media_path"))
    return bool(has_photo) and not e.get("message_text")


def _group_entries(entries: list[dict]) -> list[dict]:
    """Newest-first entries -> display groups. Consecutive photo-only
    messages from one person within a few minutes (a Telegram album arrives
    as separate messages) become one gallery, instead of dozens of
    near-identical entries."""
    groups: list[list[dict]] = []
    for entry in entries:
        if groups:
            last = groups[-1][-1]
            if (
                _is_photo_only(entry)
                and _is_photo_only(last)
                and groups[-1][0]["author_name"] == entry["author_name"]
                and abs(
                    (datetime.fromisoformat(last["posted_at"])
                     - datetime.fromisoformat(entry["posted_at"])).total_seconds()
                ) <= _PHOTO_BURST_SECONDS
            ):
                groups[-1].append(entry)
                continue
        groups.append([entry])

    return [
        {
            "ids": [e["id"] for e in g],
            "posted_at": g[0]["posted_at"],
            "author_name": g[0]["author_name"],
            "message_text": g[0].get("message_text"),
            "entries": g,
            "linked_line_item_id": g[0].get("linked_line_item_id"),
        }
        for g in groups
    ]


def _media_for(entries: list[dict], urls: dict[str, str]) -> list[dict]:
    """The viewable media of a group's entries: our private stored copy when
    there is one (survives Telegram dropping the file), else the Telegram
    file proxied through the app (older entries)."""
    media = []
    for e in entries:
        if e.get("media_path") and e["media_path"] in urls:
            media.append({"kind": e.get("media_kind") or "photo", "src": urls[e["media_path"]]})
        elif e.get("photo_file_id"):
            media.append({"kind": "photo", "src": f"/api/photos/{e['photo_file_id']}"})
    return media


@router.get("/properties/{property_id}/journal")
def get_journal(
    property_id: str,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    assert_property_access(db, user, property_id)
    prop = get_property(db, property_id, "id, property_name, archived, telegram_chat_id")
    base = "id, author_name, message_text, photo_file_id, posted_at, linked_line_item_id"
    try:
        entries = (
            db.table("journal_entries").select(base + ", media_kind, media_path")
            .eq("property_id", property_id).order("posted_at", desc=True).limit(600)
            .execute().data
        )
    except Exception:  # migration_telegram_inbox.sql not run yet
        entries = (
            db.table("journal_entries").select(base)
            .eq("property_id", property_id).order("posted_at", desc=True).limit(600)
            .execute().data
        )
    urls = signed_urls(
        db, JOURNAL_MEDIA_BUCKET,
        [e["media_path"] for e in entries if e.get("media_path")], 3600,
    )
    _, items = property_units_and_items(db, property_id, "id, task_name")
    return {
        "property": {
            "id": prop["id"], "property_name": prop["property_name"],
            "archived": bool(prop.get("archived")),
            "telegram_linked": bool(prop.get("telegram_chat_id")),
        },
        "groups": [
            {**{k: v for k, v in g.items() if k != "entries"}, "media": _media_for(g["entries"], urls)}
            for g in _group_entries(entries)
        ],
        "tasks": [{"id": i["id"], "task_name": i["task_name"]} for i in items],
    }


class LinkBody(BaseModel):
    entry_ids: list[str]
    line_item_id: str | None


@router.put("/journal/link")
def link_entries(
    body: LinkBody,
    user: dict = Depends(owner_or_pm),
    db: Client = Depends(get_supabase_client),
):
    if not body.entry_ids:
        raise HTTPException(422, "No entries given")
    rows = (
        db.table("journal_entries").select("id, property_id")
        .in_("id", body.entry_ids).execute().data
    )
    for property_id in {r["property_id"] for r in rows}:
        assert_writable(get_property(db, property_id, "id, archived"))
    db.table("journal_entries").update(
        {"linked_line_item_id": body.line_item_id}
    ).in_("id", body.entry_ids).execute()
    return {"ok": True}


@router.get("/photos/{file_id}")
def telegram_photo(file_id: str, user: dict = Depends(owner_or_pm)):
    """Serves a Telegram photo through the app. Telegram's own file URL
    embeds the bot token, so the browser must never be handed it."""
    data = download_file_bytes(file_id)
    if data is None:
        raise HTTPException(404, "Photo unavailable")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=604800, immutable"},
    )
