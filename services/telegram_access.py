"""Who may give the bots commands.

Anyone can message a Telegram bot, so commands that change data — bulk task
updates (which feed draw eligibility), linking a group to a property, linking
the daily-summary chat, and everything Matty does — are accepted only from an
allowlist of Telegram user ids. Ordinary group chatter from members of a
linked group is still journaled; it just can't command anything.

The allowlist is TELEGRAM_ALLOWED_USER_IDS (comma-separated) plus the owner's
own linked chat: in a private chat the chat id IS the user's Telegram id.
Empty allowlist = nobody can command (fails closed).
"""

import os

from supabase import Client


def allowed_user_ids(supabase: Client) -> set[str]:
    ids = {
        part.strip()
        for part in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if part.strip()
    }
    try:
        rows = (
            supabase.table("bot_state").select("value")
            .eq("key", "pm_digest_chat_id").execute().data
        )
        if rows and rows[0]["value"]:
            ids.add(str(rows[0]["value"]))
    except Exception:
        pass
    return ids


def is_allowed(supabase: Client, user_id: str | int | None) -> bool:
    return user_id is not None and str(user_id) in allowed_user_ids(supabase)
