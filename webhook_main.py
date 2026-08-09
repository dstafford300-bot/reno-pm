"""Standalone FastAPI service — the single real-time entry point for every
incoming Telegram message, for BOTH bots this app runs: Jeeves (the main
PM assistant — journal, receipts, group-linking, digests) and Matty (a
single-purpose legacy-materials log/lookup bot, no trigger phrase needed
since 100% of its traffic is material-related). Two separate Telegram
bots, two separate webhook routes, one shared process.

Runs as its own Docker service (see docker-compose's jeeves-webhook),
separate from the Streamlit app, because Telegram only allows ONE delivery
mode per bot: either polling (getUpdates) or a webhook, never both at
once. Moving Jeeves to a webhook here means the old polling-based nightly
sync (services/journal_sync.py's sync_all_journals, since removed) and the
on-demand "check for group link" buttons no longer receive anything — this
file is the replacement for all of that, event-driven instead of
batch/nightly.

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
    answer_lookup_question,
    classify_intent,
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


def _claim_update(supabase: Client, bot: str, update_id: int) -> bool:
    """True if this update should be processed (and claims it) — False
    only when it's a genuine Telegram retry of an update we've already
    handled (a primary-key conflict on (bot, update_id)). Any OTHER
    failure (e.g. the migration hasn't been run, a transient network
    error) fails OPEN — returns True so the message still gets processed
    — since the alternative is silently dropping every single incoming
    message, which is far worse than occasionally double-processing a
    retry. Keyed by bot as well as update_id because each bot has its own
    update_id counter — Jeeves' and Matty's can legitimately overlap."""
    try:
        supabase.table("processed_telegram_updates").insert(
            {"bot": bot, "update_id": update_id}
        ).execute()
        return True
    except Exception as e:
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            return False
        print(f"WARNING: could not record {bot} update {update_id} as processed: {e}")
        return True


def _handle_jeeves_update(update: dict) -> None:
    supabase = _get_supabase()

    update_id = update.get("update_id")
    if update_id is not None and not _claim_update(supabase, "jeeves", update_id):
        return

    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type")
    text = (message.get("text") or message.get("caption") or "").strip()
    text_lower = text.lower()

    properties = (
        supabase.table("properties")
        .select("id, property_name, telegram_chat_id")
        .execute()
        .data
    )

    # 1. Group-link phrase: "Jeeves Sync <property name>".
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

    # 2. PM daily-digest DM-link phrase.
    if chat_type == "private" and PM_DIGEST_VERIFICATION_PHRASE.lower() in text_lower:
        set_pm_chat_id(supabase, chat_id)
        send_telegram_message(
            chat_id, "🎩 Splendid — I shall send your daily summary here."
        )
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

    # 3. Expense receipt (photo + "jeeves receipt"/"jeeves material").
    if is_receipt_message(shaped_message):
        process_receipt_message(supabase, property_id, shaped_message)
        return

    # 4. Routine field update — log to the journal if Claude judges it
    # worth keeping.
    relevant = filter_relevant_messages([shaped_message])
    if relevant:
        entry = relevant[0]
        entry["property_id"] = property_id
        supabase.table("journal_entries").upsert(
            entry, on_conflict="telegram_chat_id,telegram_message_id"
        ).execute()


def _handle_matty_update(update: dict) -> None:
    supabase = _get_supabase()

    update_id = update.get("update_id")
    if update_id is not None and not _claim_update(supabase, "matty", update_id):
        return

    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    text = (message.get("text") or message.get("caption") or "").strip()
    photo_sizes = message.get("photo") or []
    photo_file_id = photo_sizes[-1]["file_id"] if photo_sizes else None
    matty_token = os.environ.get("MATTY_BOT_TOKEN")

    if not text and not photo_file_id:
        return

    intent = classify_intent(text, has_photo=bool(photo_file_id))

    if intent == "lookup":
        send_telegram_message(
            chat_id, answer_lookup_question(text), bot_token=matty_token
        )
        return

    image_bytes = None
    photo_url = None
    if photo_file_id:
        image_bytes = download_file_bytes(photo_file_id, bot_token=matty_token)
        if image_bytes is None:
            send_telegram_message(
                chat_id,
                "I couldn't download that photo — could you resend it?",
                bot_token=matty_token,
            )
            return
        photo_url = upload_legacy_material_photo(supabase, image_bytes)

    result = log_material_from_message(
        text, image_bytes=image_bytes, photo_public_url=photo_url
    )
    send_telegram_message(chat_id, result["confirmation_text"], bot_token=matty_token)


@app.get("/")
def health() -> dict:
    return {"status": "ok"}


def _verify_secret(expected_env_var: str, provided: str | None) -> None:
    expected_secret = os.environ.get(expected_env_var)
    if expected_secret and provided != expected_secret:
        raise HTTPException(status_code=401, detail="bad secret token")


@app.post("/telegram-webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    _verify_secret("TELEGRAM_WEBHOOK_SECRET", x_telegram_bot_api_secret_token)
    update = await request.json()
    # Reply 200 immediately — Telegram will retry on timeout, and Claude
    # calls can take several seconds, longer than Telegram's patience for
    # a synchronous webhook response.
    background_tasks.add_task(_handle_jeeves_update, update)
    return {"ok": True}


@app.post("/matty-webhook")
async def matty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    _verify_secret("MATTY_WEBHOOK_SECRET", x_telegram_bot_api_secret_token)
    update = await request.json()
    background_tasks.add_task(_handle_matty_update, update)
    return {"ok": True}
