"""Nightly job: for every property with a linked Telegram group, pull new
messages, have Jeeves (Claude) filter out chatter, and log only the
relevant field notes/photos into journal_entries. Any photo sent with the
keyword "Jeeves receipt" or "Jeeves material" is additionally re-hosted in
Supabase Storage and logged as a material_logs expense. Also checks Gmail
(if EMAIL_USER/EMAIL_PASSWORD are configured) for unread Home Depot/Lowe's
receipt emails and logs those too. Finally, sends the head PM's daily
cross-property digest, if a PM chat has been linked (Dashboard's
"🔔 Daily PM Summary" section) — a no-op otherwise.

Scheduled via macOS launchd (see scripts/com.renopm.journalsync.plist).
Safe to re-run manually at any time: ./venv/bin/python scripts/nightly_journal_sync.py

Shares its actual sync logic with the Journal page's manual sync button —
see services/journal_sync.py. Email sync uses services/email_receipts.py,
also triggerable on demand from the Budget page. PM digest logic lives in
services/pm_digest.py, also triggerable on demand from the Dashboard.
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
from services.journal_sync import sync_all_journals
from services.pm_digest import send_daily_pm_digest


def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    properties = (
        client.table("properties")
        .select("id, property_name, telegram_chat_id")
        .execute()
        .data
    )

    print(f"[{datetime.now(timezone.utc).isoformat()}] Nightly journal sync starting")
    # One shared fetch across every property, not one per-property call —
    # see sync_all_journals' docstring for why that matters now that the
    # Telegram update offset is being advanced.
    results = sync_all_journals(client, properties)
    for prop in properties:
        result = results.get(prop["id"])
        if result is None:
            continue
        print(
            f"  {prop['property_name']}: saw {result['seen']} message(s), "
            f"{result['new']} new, kept {result['kept']} after filtering, "
            f"{result['receipts']} receipt(s) logged"
        )

    email_result = sync_email_receipts(client, properties)
    print(
        f"  Email: found {email_result['found']}, processed "
        f"{email_result['processed']}, {email_result['unassigned']} unassigned"
    )

    digest_sent = send_daily_pm_digest(client)
    print(f"  PM daily digest: {'sent' if digest_sent else 'no PM chat linked, skipped'}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Nightly journal sync complete")


if __name__ == "__main__":
    main()
