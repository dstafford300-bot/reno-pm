"""Standalone FastAPI service — the single real-time entry point for every
incoming Telegram message, for BOTH bots this app runs: Jeeves (journal,
receipts, group-linking, digests, bulk schedule updates) and Matty (the
legacy-materials log/lookup bot). Two bots, two webhook routes, one process.

Reliability model (see services/inbox.py):
  1. Every update is written to telegram_inbox the instant it arrives.
  2. It is marked handled only AFTER it actually succeeds.
  3. A failure is retried by a background worker every couple of minutes;
     after repeated failures the owner is alerted on Telegram.

Security model (see services/telegram_access.py): commands that change data
are accepted only from allowlisted Telegram user ids. Ordinary group chatter
from members of a linked group is journaled but can't command anything.

Runs as its own Docker service, separate from the web app, because Telegram
allows only ONE delivery mode per bot: polling or a webhook, never both.
"""

import html
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from supabase import Client, create_client

from services import inbox
from services.bulk_status_update import (
    apply_pending_update,
    is_confirmation,
    propose_bulk_update,
)
from services.db_writer import set_property_telegram_chat_id
from services.journal_ai import filter_relevant_messages
from services.materials_log import (
    answer_lookup_question,
    classify_intent,
    log_material_from_message,
)
from services.media_store import copy_to_storage
from services.pm_digest import get_pm_chat_id, set_pm_chat_id
from services.receipt_ingest import is_receipt_message, process_receipt_message
from services.storage import upload_legacy_material_photo
from services.telegram_access import is_allowed
from services.telegram_bot import (
    PM_DIGEST_VERIFICATION_PHRASE,
    download_file_bytes,
    extract_chat_messages,
    send_telegram_message,
    verification_phrase,
)

_supabase: Client | None = None
_VOICE_KINDS = ("voice", "audio", "video", "video_note")
_MEDIA_KINDS = ("photo",) + _VOICE_KINDS + ("document", "animation")
_MEDIA_LABEL = {
    "voice": "🎤 Voice note (not transcribed)", "audio": "🎵 Audio file",
    "video": "🎥 Video", "video_note": "🎥 Video message", "document": "📎 File",
    "animation": "🎞️ Animation", "photo": None,
}


def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _supabase


def _alert_owner(supabase: Client, text: str) -> None:
    chat_id = get_pm_chat_id(supabase)
    if chat_id:
        send_telegram_message(chat_id, text)


# --------------------------------------------------------------------- Jeeves
def _journal_entry(supabase: Client, message: dict, chat_id: str, property_id: str,
                   kind: str | None) -> dict:
    """A journal_entries row for this message, with its media copied into
    private storage (a Telegram file id alone stops working if Telegram
    drops the file)."""
    entry = extract_chat_messages([{"message": message}], chat_id)[0]
    entry["property_id"] = property_id
    if kind in _MEDIA_KINDS:
        path, note = copy_to_storage(supabase, message, kind, f"{property_id}")
        entry["media_kind"] = kind
        entry["media_path"] = path
        if not entry.get("message_text"):
            label = _MEDIA_LABEL.get(kind)
            entry["message_text"] = f"{label} — {note}" if (label and note) else (note or label)
    return entry


def _handle_edit(supabase: Client, message: dict, chat_id: str, property_id: str) -> None:
    text = (message.get("text") or message.get("caption") or "").strip()
    existing = (
        supabase.table("journal_entries").select("id")
        .eq("telegram_chat_id", chat_id).eq("telegram_message_id", message["message_id"])
        .execute().data
    )
    if existing:
        edited_at = datetime.fromtimestamp(
            message.get("edit_date") or message["date"], tz=timezone.utc
        ).isoformat()
        supabase.table("journal_entries").update(
            {"message_text": text, "edited_at": edited_at}
        ).eq("id", existing[0]["id"]).execute()
        return
    # Wasn't journaled before — the edit may have turned it into a fact.
    if text:
        shaped = extract_chat_messages([{"message": message}], chat_id)
        relevant = filter_relevant_messages(shaped)
        if relevant:
            entry = relevant[0]
            entry["property_id"] = property_id
            supabase.table("journal_entries").upsert(
                entry, on_conflict="telegram_chat_id,telegram_message_id"
            ).execute()


def _dispatch_jeeves(supabase: Client, update: dict) -> None:
    message, is_edit = inbox.message_of(update)
    if not message:
        return  # joins, reactions, etc. — logged in the inbox, nothing to do

    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    chat_type = chat.get("type")
    sender_id = (message.get("from") or {}).get("id")
    text = (message.get("text") or message.get("caption") or "").strip()
    text_lower = text.lower()
    kind = inbox.kind_of(update)
    allowed = is_allowed(supabase, sender_id)

    properties = (
        supabase.table("properties").select("id, property_name, telegram_chat_id")
        .execute().data
    )

    if not is_edit:
        # Group-link phrase: takes over a property's group, so allowlisted only.
        if chat_type in ("group", "supergroup") and allowed:
            for prop in properties:
                if verification_phrase(prop["property_name"]).lower() in text_lower:
                    set_property_telegram_chat_id(supabase, prop["id"], chat_id)
                    send_telegram_message(
                        chat_id,
                        "🎩 Splendid — this group is now linked to "
                        f"<b>{html.escape(prop['property_name'])}</b>.",
                    )
                    return

        # Private chat with Jeeves: everything here is a command.
        if chat_type == "private":
            if not allowed:
                return  # strangers are ignored (still saved in the inbox)
            if PM_DIGEST_VERIFICATION_PHRASE.lower() in text_lower:
                set_pm_chat_id(supabase, chat_id)
                send_telegram_message(chat_id, "🎩 Splendid — I shall send your daily summary here.")
                return
            if kind in _VOICE_KINDS:
                send_telegram_message(
                    chat_id,
                    "🎩 I'm afraid I can't transcribe voice notes or videos yet, sir — "
                    "kindly type it out.",
                )
                return
            if kind == "text" and text:
                # Preview-then-confirm: nothing is written until an explicit yes.
                reply = apply_pending_update(supabase, chat_id) if is_confirmation(text) else None
                if reply is None:
                    reply = propose_bulk_update(supabase, chat_id, text)
                if reply is not None:
                    send_telegram_message(chat_id, reply)
            return

    # Everything below only applies to a group already linked to a property.
    property_row = next(
        (p for p in properties if str(p.get("telegram_chat_id")) == chat_id), None
    )
    if not property_row:
        return
    property_id = property_row["id"]

    if is_edit:
        _handle_edit(supabase, message, chat_id, property_id)
        return

    if kind == "other":
        return  # stickers, polls, etc. — saved in the inbox only

    shaped = extract_chat_messages([update], chat_id)
    if not shaped:
        return

    if kind == "photo" and is_receipt_message(shaped[0]):
        process_receipt_message(supabase, property_id, shaped[0])
        return

    if kind in _MEDIA_KINDS:
        # Every photo/voice note/video is a site record: keep it, stored privately.
        entry = _journal_entry(supabase, message, chat_id, property_id, kind)
    else:
        relevant = filter_relevant_messages([shaped[0]])
        if not relevant:
            return
        entry = relevant[0]
        entry["property_id"] = property_id
    supabase.table("journal_entries").upsert(
        entry, on_conflict="telegram_chat_id,telegram_message_id"
    ).execute()


# ----------------------------------------------------------------------- Matty
def _dispatch_matty(supabase: Client, update: dict) -> None:
    message, is_edit = inbox.message_of(update)
    if not message or is_edit:
        return

    matty_token = os.environ.get("MATTY_BOT_TOKEN")
    chat_id = str(message.get("chat", {}).get("id"))
    sender_id = (message.get("from") or {}).get("id")

    if not is_allowed(supabase, sender_id):
        send_telegram_message(
            chat_id, "Sorry — I only take instructions from my owner.", bot_token=matty_token
        )
        return

    kind = inbox.kind_of(update)
    if kind in _VOICE_KINDS:
        send_telegram_message(
            chat_id,
            "I can't read voice notes or videos yet — please send a photo of the label, "
            "or type the details.",
            bot_token=matty_token,
        )
        return

    text = (message.get("text") or message.get("caption") or "").strip()
    photo_sizes = message.get("photo") or []
    photo_file_id = photo_sizes[-1]["file_id"] if photo_sizes else None
    if not text and not photo_file_id:
        return

    if classify_intent(text, has_photo=bool(photo_file_id)) == "lookup":
        send_telegram_message(
            chat_id, answer_lookup_question(text, supabase=supabase), bot_token=matty_token
        )
        return

    image_bytes = photo_url = None
    if photo_file_id:
        image_bytes = download_file_bytes(photo_file_id, bot_token=matty_token)
        if image_bytes is None:
            send_telegram_message(
                chat_id, "I couldn't download that photo — could you resend it?",
                bot_token=matty_token,
            )
            return
        photo_url = upload_legacy_material_photo(supabase, image_bytes)

    result = log_material_from_message(text, image_bytes=image_bytes, photo_public_url=photo_url)
    send_telegram_message(chat_id, result["confirmation_text"], bot_token=matty_token)


# ------------------------------------------------- processing, retry, alerting
def _process(bot: str, row: dict) -> None:
    supabase = _get_supabase()
    dispatch = _dispatch_jeeves if bot == "jeeves" else _dispatch_matty
    try:
        dispatch(supabase, row["payload"])
    except Exception as e:
        status = inbox.mark_failed(supabase, row, e)
        print(f"ERROR {bot} update {row['payload'].get('update_id')} -> {status}: {inbox.redact(repr(e))}")
        if status == "dead":
            try:
                _alert_owner(
                    supabase,
                    f"⚠️ I couldn't process a Telegram message after {inbox.MAX_ATTEMPTS} "
                    f"tries ({bot}, chat {row.get('chat_id')}): {html.escape(inbox.redact(str(e))[:200])}\n\n"
                    "The message itself is saved in the inbox — nothing is lost.",
                )
            except Exception as alert_error:
                print(f"ERROR alerting owner: {inbox.redact(repr(alert_error))}")
        return
    inbox.mark_handled(supabase, row)


def _retry_loop(stop: threading.Event) -> None:
    while not stop.wait(120):
        try:
            for row in inbox.due_for_retry(_get_supabase()):
                _process(row["bot"], row)
        except Exception as e:
            print(f"ERROR in retry loop: {inbox.redact(repr(e))}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_retry_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def health() -> dict:
    return {"status": "ok"}


def _verify_secret(expected_env_var: str, provided: str | None) -> None:
    expected_secret = os.environ.get(expected_env_var)
    if expected_secret and provided != expected_secret:
        raise HTTPException(status_code=401, detail="bad secret token")


async def _receive(bot: str, request: Request, background_tasks: BackgroundTasks) -> dict:
    update = await request.json()
    # Saved BEFORE we answer, so even a crash right after can't lose it.
    row = await run_in_threadpool(inbox.record, _get_supabase(), bot, update)
    if row is not None:
        # Reply 200 immediately; Claude calls can outlast Telegram's patience.
        background_tasks.add_task(_process, bot, row)
    return {"ok": True}


@app.post("/telegram-webhook")
async def telegram_webhook(
    request: Request, background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    _verify_secret("TELEGRAM_WEBHOOK_SECRET", x_telegram_bot_api_secret_token)
    return await _receive("jeeves", request, background_tasks)


@app.post("/matty-webhook")
async def matty_webhook(
    request: Request, background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    _verify_secret("MATTY_WEBHOOK_SECRET", x_telegram_bot_api_secret_token)
    return await _receive("matty", request, background_tasks)
