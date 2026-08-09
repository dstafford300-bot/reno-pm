"""One-off: registers a webhook_main.py endpoint with Telegram, replacing
polling. Run manually after the jeeves-webhook container is deployed and
confirmed reachable — see webhook_main.py's module docstring for context.

Usage:
  WEBHOOK_URL=https://.../telegram-webhook python scripts/register_telegram_webhook.py
  WEBHOOK_URL=https://.../matty-webhook BOT=matty python scripts/register_telegram_webhook.py

BOT defaults to "jeeves" (using TELEGRAM_BOT_TOKEN/TELEGRAM_WEBHOOK_SECRET);
set BOT=matty to register Matty instead (using MATTY_BOT_TOKEN/
MATTY_WEBHOOK_SECRET). Reads from the environment (same app.env the
containers use).
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

    bot = os.environ.get("BOT", "jeeves")
    if bot == "matty":
        bot_token = os.environ.get("MATTY_BOT_TOKEN")
        secret = os.environ.get("MATTY_WEBHOOK_SECRET")
    else:
        bot_token = None  # set_webhook falls back to TELEGRAM_BOT_TOKEN
        secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

    ok = set_webhook(url, secret_token=secret, bot_token=bot_token)
    print(f"{bot}: webhook registered." if ok else f"{bot}: failed to register webhook.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
