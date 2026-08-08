"""Nightly job: checks Gmail (if EMAIL_USER/EMAIL_PASSWORD are configured)
for unread Home Depot/Lowe's receipt emails and logs those, checks every
task with dependencies for a 48h/24h trade look-ahead nudge, and sends the
head PM's daily cross-property digest if a PM chat has been linked
(Dashboard's "🔔 Daily PM Summary" section) — a no-op otherwise.

Journal message logging and Telegram-photo receipt ingestion used to run
here too, but now happen in real time via the webhook (see webhook_main.py)
instead of a once-nightly poll — Telegram only allows one delivery mode
per bot (polling OR webhook, never both), so that logic moved there
entirely rather than running in both places.

Scheduled via GitHub Actions (see .github/workflows/nightly-sync.yml).
Safe to re-run manually at any time: ./venv/bin/python scripts/nightly_journal_sync.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import os

from supabase import create_client

from services.email_receipts import sync_email_receipts
from services.pm_digest import send_daily_pm_digest
from services.trade_nudges import check_and_send_trade_nudges


def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    properties = (
        client.table("properties")
        .select("id, property_name, telegram_chat_id")
        .execute()
        .data
    )

    print(f"[{datetime.now(timezone.utc).isoformat()}] Nightly sync starting")

    email_result = sync_email_receipts(client, properties)
    print(
        f"  Email: found {email_result['found']}, processed "
        f"{email_result['processed']}, {email_result['unassigned']} unassigned"
    )

    nudge_result = check_and_send_trade_nudges(client, properties)
    print(
        f"  Trade nudges: checked {nudge_result['checked']} task(s), "
        f"sent {nudge_result['sent']}"
    )

    digest_sent = send_daily_pm_digest(client)
    print(f"  PM daily digest: {'sent' if digest_sent else 'no PM chat linked, skipped'}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Nightly sync complete")


if __name__ == "__main__":
    main()
