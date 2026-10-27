"""The Telegram inbox: every update is stored the moment it arrives, then
marked handled only after it actually succeeds. A failure is retried and, if
it keeps failing, the owner is alerted — instead of the message silently
vanishing. See scripts/migration_telegram_inbox.sql."""

import os
from datetime import datetime, timedelta, timezone

from supabase import Client

MAX_ATTEMPTS = 5
RETRY_AFTER = timedelta(minutes=3)

_MEDIA_KINDS = ("photo", "voice", "video", "video_note", "audio", "document", "animation")


_SECRET_ENV = (
    "TELEGRAM_BOT_TOKEN", "MATTY_BOT_TOKEN", "ANTHROPIC_API_KEY", "SUPABASE_KEY",
    "AIRTABLE_API_KEY", "APP_JWT_SECRET", "ACCESS_TOKEN", "EMAIL_PASSWORD",
    "TELEGRAM_WEBHOOK_SECRET", "MATTY_WEBHOOK_SECRET",
)


def redact(text: str) -> str:
    """Strips any secret out of an error message before it is stored or sent
    — a failed Telegram request's message can contain the bot token."""
    for name in _SECRET_ENV:
        value = os.environ.get(name)
        if value and len(value) > 8:
            text = text.replace(value, "***")
    return text


def message_of(update: dict) -> tuple[dict | None, bool]:
    """(message, is_edit) — channel posts and edited messages included."""
    for key, is_edit in (
        ("message", False), ("channel_post", False),
        ("edited_message", True), ("edited_channel_post", True),
    ):
        if update.get(key):
            return update[key], is_edit
    return None, False


def kind_of(update: dict) -> str:
    message, is_edit = message_of(update)
    if message is None:
        return "other"
    if is_edit:
        return "edited"
    for kind in _MEDIA_KINDS:
        if message.get(kind):
            return kind
    return "text" if message.get("text") else "other"


def _fields(bot: str, update: dict) -> dict:
    message, _ = message_of(update)
    message = message or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    return {
        "bot": bot,
        "update_id": update.get("update_id"),
        "chat_id": str(chat["id"]) if chat.get("id") is not None else None,
        "chat_type": chat.get("type"),
        "user_id": str(sender["id"]) if sender.get("id") is not None else None,
        "message_id": message.get("message_id"),
        "kind": kind_of(update),
        "text": message.get("text") or message.get("caption"),
        "payload": update,
    }


def record(supabase: Client, bot: str, update: dict) -> dict | None:
    """Stores the update. Returns the new row; None if Telegram re-delivered
    one already stored (the original row is, or will be, handled — don't
    process twice); or {"id": None} if the inbox table isn't available, in
    which case the caller should still process the update rather than drop
    it."""
    try:
        return supabase.table("telegram_inbox").insert(_fields(bot, update)).execute().data[0]
    except Exception as e:
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            return None
        print(f"WARNING: couldn't log {bot} update {update.get('update_id')}: {e}")
        return {"id": None, "attempts": 0, "payload": update, "bot": bot}


def mark_handled(supabase: Client, row: dict) -> None:
    if row.get("id") is None:
        return
    try:
        supabase.table("telegram_inbox").update(
            {
                "status": "handled", "attempts": (row.get("attempts") or 0) + 1,
                "handled_at": datetime.now(timezone.utc).isoformat(), "last_error": None,
            }
        ).eq("id", row["id"]).execute()
    except Exception as e:
        print(f"WARNING: couldn't mark inbox row {row['id']} handled: {e}")


def mark_failed(supabase: Client, row: dict, error: Exception) -> str:
    """Returns the new status: "failed" (will retry) or "dead" (gave up)."""
    attempts = (row.get("attempts") or 0) + 1
    status = "dead" if attempts >= MAX_ATTEMPTS else "failed"
    if row.get("id") is not None:
        try:
            supabase.table("telegram_inbox").update(
                {"status": status, "attempts": attempts, "last_error": redact(str(error))[:1000]}
            ).eq("id", row["id"]).execute()
        except Exception as e:
            print(f"WARNING: couldn't mark inbox row {row['id']} failed: {e}")
    return status


def due_for_retry(supabase: Client, limit: int = 20) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - RETRY_AFTER).isoformat()
    try:
        return (
            supabase.table("telegram_inbox").select("*")
            .in_("status", ["received", "failed"])
            .lt("attempts", MAX_ATTEMPTS).lt("received_at", cutoff)
            .order("id").limit(limit).execute().data
        )
    except Exception:
        return []
