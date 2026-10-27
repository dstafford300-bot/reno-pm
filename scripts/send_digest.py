"""Sends the PM's digest. Run by the server's cron at 6am Pacific, Mondays, Wednesdays and Fridays."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import os

from supabase import create_client

from datetime import datetime
from zoneinfo import ZoneInfo

from services.pm_digest import send_daily_pm_digest

# Mondays cover Fri→Mon; Wednesdays and Fridays cover the 2 days before.
hours = 72 if datetime.now(ZoneInfo("America/Los_Angeles")).weekday() == 0 else 48
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
print("digest sent" if send_daily_pm_digest(client, hours=hours) else "no PM chat linked, skipped")
