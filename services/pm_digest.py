"""Cross-property daily digest for the head PM — one Telegram DM per day
(not real-time; that would just duplicate each property's own group
alerts) summarizing schedule changes, draw releases, journal activity,
and material purchases across every property.
"""

from datetime import date, datetime, timedelta, timezone

from supabase import Client

from services.db_writer import (
    get_line_item_cost_variance,
    get_milestone_task_progress,
    milestone_is_eligible,
)
from services.telegram_bot import format_daily_digest_message, send_daily_digest

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


def build_action_items(client: Client, property_id: str) -> list[str]:
    """Standing, always-relevant action items for one property — unlike
    the rest of the digest, these aren't scoped to the last 24 hours, so
    an overdue task or a draw that's been eligible for a week still shows
    up every morning until it's actually dealt with.

    Three checks, all built on data/logic that already exists elsewhere
    in the app rather than anything new:
      - Draws whose linked tasks have all met their required completion
        % (milestone_is_eligible) but haven't been released yet.
      - Tasks past their estimated_end_date that aren't Completed.
      - Tasks whose logged material spend exceeds budgeted_cost (negative
        variance from get_line_item_cost_variance).
    """
    items: list[str] = []

    try:
        milestones = (
            client.table("draw_milestones")
            .select("id, milestone_name, draw_amount, status")
            .eq("property_id", property_id)
            .neq("status", "Released")
            .execute()
            .data
        )
        for m in milestones:
            progress = get_milestone_task_progress(client, m["id"])
            if milestone_is_eligible(progress):
                items.append(
                    f"💰 Draw ready to authorize: {m['milestone_name']} "
                    f"(${m['draw_amount']:,.2f})"
                )
    except Exception:
        pass  # draw-tracking migrations not run yet

    try:
        units = (
            client.table("units").select("id").eq("property_id", property_id).execute().data
        )
        unit_ids = [u["id"] for u in units]
        if unit_ids:
            today = date.today().isoformat()
            overdue = (
                client.table("line_items")
                .select("task_name, estimated_end_date, status")
                .in_("unit_id", unit_ids)
                .lt("estimated_end_date", today)
                .neq("status", "Completed")
                .execute()
                .data
            )
            for task in overdue:
                items.append(
                    f"⚠️ Overdue: {task['task_name']} "
                    f"(was due {task['estimated_end_date']})"
                )
    except Exception:
        pass

    try:
        for row in get_line_item_cost_variance(client, property_id):
            if row["variance"] < 0:
                items.append(
                    f"📈 Over budget: {row['unit_name']}: {row['task_name']} — "
                    f"${abs(row['variance']):,.2f} over"
                )
    except Exception:
        pass  # material_line_item migration not run yet

    return items


def build_daily_digest(client: Client, hours: int = 24) -> list[dict]:
    """Gathers standing action items plus the last `hours` of activity
    across every property into
    [{"property_name": ..., "action_items": [...], "activity_lines": [...]}],
    omitting properties with neither.

    Pulls recent activity from activity_log (the durable record written
    alongside each existing per-property Telegram alert — see
    services/db_writer.py's log_activity) plus journal_entries and
    material_logs directly, since those two don't otherwise funnel
    through activity_log.
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

    action_items_by_property = {
        pid: build_action_items(client, pid) for pid in property_names
    }

    all_property_ids = set(lines_by_property) | {
        pid for pid, items in action_items_by_property.items() if items
    }

    return [
        {
            "property_name": property_names[pid],
            "action_items": action_items_by_property.get(pid, []),
            "activity_lines": lines_by_property.get(pid, []),
        }
        for pid in all_property_ids
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
