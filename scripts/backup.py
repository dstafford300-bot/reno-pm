"""Nightly off-server backup of the Supabase database AND storage files.

  python scripts/backup.py            -> uploads to Backblaze B2 (needs B2_* env)
  python scripts/backup.py --local D  -> writes to folder D (for testing)

What it saves
  db/<date>.tar.gz.enc   every table as JSON lines, tarred + encrypted
  files/<bucket>/<path>  every file in the Supabase Storage buckets
                         (encrypted; only files not already backed up are sent)
Keeps the newest KEEP_DB database backups; files are kept (they never change).

Everything is encrypted with BACKUP_ENCRYPTION_KEY (Fernet) BEFORE it leaves
this machine — the data includes receipts and password hashes. Keep that key
somewhere safe OUTSIDE this repo: lose it and the backups are unreadable.

This runs in a public repo's GitHub Actions, so it must never print data —
only counts and names of tables/buckets.
"""

import argparse
import io
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet

from db.connection import get_supabase_client

KEEP_DB = 30
PAGE = 1000
BUCKETS = ["journal-media", "receipts", "legacy-materials"]
# table -> column used to page through it in a stable order
TABLES = {
    "properties": "id", "units": "id", "line_items": "id", "draw_milestones": "id",
    "draw_milestone_tasks": "id", "journal_entries": "id", "material_logs": "id",
    "activity_log": "id", "bot_state": "key", "telegram_inbox": "id",
    "processed_telegram_updates": "update_id", "processed_emails": "message_id",
    "pending_bulk_updates": "chat_id", "app_users": "id", "user_properties": "user_id",
    "change_requests": "id", "pending_schedule_changes": "id",
}


# ------------------------------------------------------------------ storage
class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def keys(self, prefix: str) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")
                      if p.is_file() and str(p.relative_to(self.root)).startswith(prefix))

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


class B2Store:
    """Backblaze B2 through its S3-compatible API."""

    def __init__(self):
        import boto3

        self.bucket = os.environ["B2_BUCKET"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["B2_ENDPOINT"],
            aws_access_key_id=os.environ["B2_KEY_ID"],
            aws_secret_access_key=os.environ["B2_APP_KEY"],
        )

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def keys(self, prefix: str) -> list[str]:
        out, token = [], None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            out += [o["Key"] for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return sorted(out)
            token = page["NextContinuationToken"]

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


# ------------------------------------------------------------------ export
def export_tables(db) -> tuple[bytes, dict[str, int]]:
    """Every table as JSON lines inside one tar.gz. Returns (archive, counts)."""
    counts: dict[str, int] = {}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for table, order_col in TABLES.items():
            rows: list[dict] = []
            try:
                start = 0
                while True:
                    page = (db.table(table).select("*").order(order_col)
                            .range(start, start + PAGE - 1).execute().data)
                    rows += page
                    if len(page) < PAGE:
                        break
                    start += PAGE
            except Exception:
                continue  # table doesn't exist (yet) in this database
            counts[table] = len(rows)
            payload = "\n".join(json.dumps(r, default=str) for r in rows).encode()
            info = tarfile.TarInfo(f"{table}.jsonl")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue(), counts


def list_objects(db, bucket: str, prefix: str = "") -> list[str]:
    """Every object path in a bucket (Supabase lists one folder level at a time)."""
    paths: list[str] = []
    offset = 0
    while True:
        items = db.storage.from_(bucket).list(prefix, {"limit": PAGE, "offset": offset})
        for item in items:
            name = f"{prefix}/{item['name']}" if prefix else item["name"]
            if item.get("id") is None:          # a folder
                paths += list_objects(db, bucket, name)
            else:
                paths.append(name)
        if len(items) < PAGE:
            return paths
        offset += PAGE


def backup_files(db, store, fernet: Fernet) -> tuple[int, int]:
    """Copies storage files not yet in the backup. Returns (new, total)."""
    new = total = 0
    for bucket in BUCKETS:
        try:
            paths = list_objects(db, bucket)
        except Exception:
            continue
        have = set(store.keys(f"files/{bucket}/"))
        for path in paths:
            total += 1
            key = f"files/{bucket}/{path}"
            if key in have:
                continue
            store.put(key, fernet.encrypt(db.storage.from_(bucket).download(path)))
            new += 1
    return new, total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", help="write to this folder instead of B2")
    args = parser.parse_args()

    fernet = Fernet(os.environ["BACKUP_ENCRYPTION_KEY"])
    store = LocalStore(args.local) if args.local else B2Store()
    db = get_supabase_client()

    archive, counts = export_tables(db)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store.put(f"db/{stamp}.tar.gz.enc", fernet.encrypt(archive))
    print(f"database: {len(counts)} tables, {sum(counts.values())} rows -> db/{stamp}.tar.gz.enc")
    for table, n in counts.items():
        print(f"  {table}: {n}")

    new, total = backup_files(db, store, fernet)
    print(f"files: {new} new of {total} total")

    old = store.keys("db/")[:-KEEP_DB]
    for key in old:
        store.delete(key)
    print(f"pruned {len(old)} old database backup(s); keeping {min(len(store.keys('db/')), KEEP_DB)}")


if __name__ == "__main__":
    main()
