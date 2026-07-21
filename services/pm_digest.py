"""Cross-property daily digest for the head PM — one Telegram DM per day
(not real-time; that would just duplicate each property's own group
alerts) summarizing schedule changes, draw releases, journal activity,
and material purchases across every property.
"""

from datetime import datetime, timedelta, timezone

from supabase import Client

from services.telegram_bot import (
    PM_DIGEST_VERIFICATION_PHRASE,
    find_private_chat_by_phrase,
    format_daily_digest_message,
    send_daily_digest,
)

_PM_CHAT_KEY = "pm_digest_chat_id"


def get_pm_chat_id(client: Client) -> str | None:
    try:
        rows = (
            client.table("bot_state")
            .select("value")
            .eq("key", _PM_CHAT_KEY)
            .execute()
            .data
        )
    except Exception:
        return None
    return rows[0]["value"] if rows else None


def set_pm_chat_id(client: Client, chat_id: str) -> None:
    client.table("bot_state").upsert(
        {"key": _PM_CHAT_KEY, "value": str(chat_id)}
    ).execute()


def clear_pm_chat_id(client: Client) -> None:
    client.table("bot_state").delete().eq("key", _PM_CHAT_KEY).execute()


def link_pm_chat(client: Client) -> dict | None:
    """Checks recent Telegram updates for the PM verification phrase sent
    in a private DM with the bot, and stores the resulting chat_id if
    found. Returns the match dict, or None if the phrase hasn't been
    seen."""
    match = find_private_chat_by_phrase(PM_DIGEST_VERIFICATION_PHRASE)
    if match:
        set_pm_chat_id(client, match["chat_id"])
    return match


def build_daily_digest(client: Client, hours: int = 24) -> list[dict]:
    """Gathers the last `hours` of activity across every property into
    [{"property_name": ..., "lines": [...]}], omitting properties with no
    activity in the window.

    Pulls from activity_log (the durable record written alongside each
    existing per-property Telegram alert — see services/db_writer.py's
    log_activity) plus journal_entries and material_logs directly, since
    those two don't otherwise funnel through activity_log.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    properties = client.table("properties").select("id, property_name").execute().data
    property_names = {p["id"]: p["property_name"] for p in properties}

    lines_by_property: dict[str, list[str]] = {}

    def _add(property_id: str | None, line: str) -> None:
        if property_id not in property_names:
            return
        lines_by_property.setdefault(property_id, []).append(line)

    try:
        activity = (
            client.table("activity_log")
            .select("property_id, summary, created_at")
            .gte("created_at", since)
            .order("created_at")
            .execute()
            .data
        )
    except Exception:
        activity = []  # migration not run yet — digest still works, just thinner
    for row in activity:
        _add(row["property_id"], row["summary"])

    journal_entries = (
        client.table("journal_entries")
        .select("property_id, posted_at")
        .gte("posted_at", since)
        .execute()
        .data
    )
    journal_counts: dict[str, int] = {}
    for entry in journal_entries:
        pid = entry.get("property_id")
        if pid:
            journal_counts[pid] = journal_counts.get(pid, 0) + 1
    for pid, count in journal_counts.items():
        noun = "entry" if count == 1 else "entries"
        _add(pid, f"{count} journal {noun} logged")

    material_logs = (
        client.table("material_logs")
        .select("property_id, amount, created_at")
        .gte("created_at", since)
        .execute()
        .data
    )
    material_totals: dict[str, float] = {}
    for log in material_logs:
        pid = log.get("property_id")
        if pid:
            material_totals[pid] = material_totals.get(pid, 0) + (log.get("amount") or 0)
    for pid, total in material_totals.items():
        _add(pid, f"${total:,.2f} in material purchases logged")

    return [
        {"property_name": property_names[pid], "lines": lines}
        for pid, lines in lines_by_property.items()
        if lines
    ]


def send_daily_pm_digest(client: Client, hours: int = 24) -> bool:
    """Builds and sends the digest to the linked PM chat. Returns False
    (no-op, not an error) if no PM chat is linked yet."""
    chat_id = get_pm_chat_id(client)
    if not chat_id:
        return False
    summaries = build_daily_digest(client, hours=hours)
    text = format_daily_digest_message(summaries)
    return send_daily_digest(text, chat_id)
