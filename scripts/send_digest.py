"""Sends the PM's morning digest. Run by the server's cron at 7am Pacific."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import os

from supabase import create_client

from services.pm_digest import send_daily_pm_digest

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
print("digest sent" if send_daily_pm_digest(client) else "no PM chat linked, skipped")
