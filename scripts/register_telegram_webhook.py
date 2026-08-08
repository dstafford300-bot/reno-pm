"""One-off: registers webhook_main.py's endpoint with Telegram, replacing
polling. Run manually after the jeeves-webhook container is deployed and
confirmed reachable — see webhook_main.py's module docstring for context.

Usage: WEBHOOK_URL=https://... python scripts/register_telegram_webhook.py
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from the environment
(same app.env the containers use).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from services.telegram_bot import set_webhook


def main():
    url = os.environ.get("WEBHOOK_URL")
    if not url:
        print("Set WEBHOOK_URL first, e.g. https://jeeves-hook.example.com/telegram-webhook")
        sys.exit(1)
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    ok = set_webhook(url, secret_token=secret)
    print("Webhook registered." if ok else "Failed to register webhook.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
