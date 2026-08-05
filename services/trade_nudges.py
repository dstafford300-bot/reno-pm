"""Look-ahead nudges: warns a trade's group chat 48 and 24 hours before
their task is predicted to be ready to start, based on when its
predecessor task(s) — via line_items.dependencies — are estimated to
finish. Runs as part of the nightly job (see scripts/nightly_journal_sync.py)
and is also triggerable on demand from the Schedule page, same pattern as
the other background syncs in this app.
"""

from datetime import date, timedelta

from supabase import Client

from services.telegram_bot import send_trade_lookahead_alert

LOOKAHEAD_TIERS = [(2, "nudge_48h_sent", 48), (1, "nudge_24h_sent", 24)]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def check_and_send_trade_nudges(client: Client, properties: list[dict]) -> dict:
    """properties: [{"id": ..., "property_name": ..., "telegram_chat_id": ...}, ...].

    For every task with dependencies, works out the latest predecessor
    estimated_end_date (a task can't start until ALL its predecessors are
    done). If that date is exactly 2 or 1 days out and this task hasn't
    started yet (status still "Pending"), sends a one-time look-ahead
    alert to the task's unit chat (falling back to the property chat),
    and flags it so it won't fire again on the same tier tomorrow.

    Returns {"checked": n, "sent": n}. Never raises — a missed nudge
    shouldn't fail the nightly job; see sync_email_receipts/sync_all_journals
    for the same best-effort pattern.
    """
    checked = sent = 0
    today = date.today()

    for prop in properties:
        property_id = prop["id"]
        property_name = prop["property_name"]
        property_chat_id = prop.get("telegram_chat_id")

        try:
            units = (
                client.table("units")
                .select("id, unit_name, telegram_chat_id")
                .eq("property_id", property_id)
                .execute()
                .data
            )
            unit_ids = [u["id"] for u in units]
            if not unit_ids:
                continue
            unit_by_id = {u["id"]: u for u in units}

            items = (
                client.table("line_items")
                .select(
                    "id, unit_id, task_name, status, estimated_end_date, "
                    "dependencies, nudge_48h_sent, nudge_24h_sent"
                )
                .in_("unit_id", unit_ids)
                .execute()
                .data
            )
        except Exception:
            continue  # migration not run yet, or table shape unexpected

        items_by_id = {item["id"]: item for item in items}

        for item in items:
            dependencies = item.get("dependencies") or []
            if not dependencies:
                continue
            if (item.get("status") or "Pending").strip().lower() != "pending":
                continue
            checked += 1

            predecessor_dates = [
                _parse_date(items_by_id[dep_id]["estimated_end_date"])
                for dep_id in dependencies
                if dep_id in items_by_id
            ]
            predecessor_dates = [d for d in predecessor_dates if d]
            if not predecessor_dates:
                continue
            predicted_start = max(predecessor_dates)
            days_out = (predicted_start - today).days

            for tier_days, flag_column, hours_out in LOOKAHEAD_TIERS:
                if days_out != tier_days or item.get(flag_column):
                    continue
                unit = unit_by_id.get(item["unit_id"], {})
                ok = send_trade_lookahead_alert(
                    property_name=property_name,
                    unit_name=unit.get("unit_name", "Unknown unit"),
                    task_name=item["task_name"],
                    predicted_start=predicted_start.isoformat(),
                    hours_out=hours_out,
                    chat_id=unit.get("telegram_chat_id") or property_chat_id,
                )
                if ok:
                    try:
                        client.table("line_items").update(
                            {flag_column: True}
                        ).eq("id", item["id"]).execute()
                    except Exception:
                        pass
                    sent += 1
                break  # a task only matches one tier per run

    return {"checked": checked, "sent": sent}
