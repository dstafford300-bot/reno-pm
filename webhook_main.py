"""Standalone FastAPI service — the single real-time entry point for every
incoming Telegram message Jeeves handles. Runs as its own Docker service
(see docker-compose's jeeves-webhook), separate from the Streamlit app,
because Telegram only allows ONE delivery mode per bot: either polling
(getUpdates) or a webhook, never both at once. Moving to a webhook here
means the old polling-based nightly sync (services/journal_sync.py's
sync_all_journals) and the on-demand "check for group link" buttons no
longer receive anything — this file is the replacement for all of that,
just event-driven instead of batch/nightly.

What still runs on the nightly cron (scripts/nightly_journal_sync.py):
email receipt scanning, the PM daily digest, and trade look-ahead nudges
— none of those depend on reading Telegram messages, so they're unaffected.
"""

import html
import os

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from supabase import Client, create_client

from services.db_writer import set_property_telegram_chat_id
from services.journal_ai import filter_relevant_messages
from services.materials_log import (
    LEGACY_TRIGGER,
    answer_lookup_question,
    is_lookup_question,
    log_material_from_message,
)
from services.pm_digest import set_pm_chat_id
from services.receipt_ingest import is_receipt_message, process_receipt_message
from services.storage import upload_legacy_material_photo
from services.telegram_bot import (
    PM_DIGEST_VERIFICATION_PHRASE,
    download_file_bytes,
    extract_chat_messages,
    send_telegram_message,
    verification_phrase,
)

app = FastAPI()

_supabase: Client | None = None


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
        )
    return _supabase


def _claim_update(supabase: Client, update_id: int) -> bool:
    """True if this update hasn't been processed yet (and claims it) —
    False if it's a Telegram retry of an update we've already handled.
    Relies on processed_telegram_updates.update_id being a primary key;
    the insert simply fails (harmlessly) on a duplicate."""
    try:
        supabase.table("processed_telegram_updates").insert(
            {"update_id": update_id}
        ).execute()
        return True
    except Exception:
        return False


def _handle_update(update: dict) -> None:
    supabase = _get_supabase()

    update_id = update.get("update_id")
    if update_id is not None and not _claim_update(supabase, update_id):
        return

    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type")
    text = (message.get("text") or message.get("caption") or "").strip()
    text_lower = text.lower()
    photo_sizes = message.get("photo") or []
    photo_file_id = photo_sizes[-1]["file_id"] if photo_sizes else None

    properties = (
        supabase.table("properties")
        .select("id, property_name, telegram_chat_id")
        .execute()
        .data
    )

    # 1. Legacy materials log photo.
    if photo_file_id and text_lower.startswith(LEGACY_TRIGGER):
        image_bytes = download_file_bytes(photo_file_id)
        if image_bytes is None:
            send_telegram_message(
                chat_id, "🎩 I regret I couldn't download that photo, sir."
            )
            return
        caption = text[len(LEGACY_TRIGGER):].lstrip(" -:—").strip()
        photo_url = upload_legacy_material_photo(supabase, image_bytes)
        result = log_material_from_message(image_bytes, caption, photo_url)
        send_telegram_message(chat_id, result["confirmation_text"])
        return

    # 2. Group-link phrase: "Jeeves Sync <property name>".
    if chat_type in ("group", "supergroup"):
        for prop in properties:
            if verification_phrase(prop["property_name"]).lower() in text_lower:
                set_property_telegram_chat_id(supabase, prop["id"], chat_id)
                send_telegram_message(
                    chat_id,
                    "🎩 Splendid — this group is now linked to "
                    f"<b>{html.escape(prop['property_name'])}</b>.",
                )
                return

    # 3. PM daily-digest DM-link phrase.
    if chat_type == "private" and PM_DIGEST_VERIFICATION_PHRASE.lower() in text_lower:
        set_pm_chat_id(supabase, chat_id)
        send_telegram_message(
            chat_id, "🎩 Splendid — I shall send your daily summary here."
        )
        return

    # 4. Plain-English lookup question ("Jeeves, what paint color...").
    if is_lookup_question(text):
        send_telegram_message(chat_id, answer_lookup_question(text))
        return

    # Everything below only applies to a group already linked to a property.
    property_row = next(
        (p for p in properties if str(p.get("telegram_chat_id")) == chat_id), None
    )
    if not property_row:
        return
    property_id = property_row["id"]

    shaped = extract_chat_messages([update], chat_id)
    if not shaped:
        return
    shaped_message = shaped[0]

    # 5. Expense receipt (photo + "jeeves receipt"/"jeeves material").
    if is_receipt_message(shaped_message):
        process_receipt_message(supabase, property_id, shaped_message)
        return

    # 6. Routine field update — log to the journal if Claude judges it
    # worth keeping.
    relevant = filter_relevant_messages([shaped_message])
    if relevant:
        entry = relevant[0]
        entry["property_id"] = property_id
        supabase.table("journal_entries").upsert(
            entry, on_conflict="telegram_chat_id,telegram_message_id"
        ).execute()


@app.get("/")
def health() -> dict:
    return {"status": "ok"}


@app.post("/telegram-webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="bad secret token")

    update = await request.json()
    # Reply 200 immediately — Telegram will retry on timeout, and Claude
    # Vision + Airtable calls can take several seconds, longer than
    # Telegram's patience for a synchronous webhook response.
    background_tasks.add_task(_handle_update, update)
    return {"ok": True}
