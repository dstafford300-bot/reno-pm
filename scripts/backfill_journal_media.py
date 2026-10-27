"""One-off: copy photos that exist only as Telegram file ids into private
storage (journal_entries.media_path), so they survive Telegram dropping them.

Run after scripts/migration_telegram_inbox.sql. Safe to re-run: skips entries
already copied, and reports any Telegram no longer serves.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_supabase_client
from services.storage import upload_journal_media
from services.telegram_bot import download_file_bytes


def main() -> None:
    db = get_supabase_client()
    rows = (
        db.table("journal_entries")
        .select("id, property_id, photo_file_id")
        .not_.is_("photo_file_id", "null")
        .is_("media_path", "null")
        .execute()
        .data
    )
    print(f"{len(rows)} photo(s) to copy")
    saved = missing = 0
    for row in rows:
        data = download_file_bytes(row["photo_file_id"])
        if data is None:
            missing += 1
            continue
        path = upload_journal_media(db, data, "image/jpeg", str(row["property_id"]), "jpg")
        db.table("journal_entries").update(
            {"media_kind": "photo", "media_path": path}
        ).eq("id", row["id"]).execute()
        saved += 1
    print(f"copied {saved}; {missing} no longer available from Telegram")


if __name__ == "__main__":
    main()
