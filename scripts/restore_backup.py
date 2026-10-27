"""Decrypt a backup so it can be inspected or restored.

  python scripts/restore_backup.py db/2026-10-26.tar.gz.enc OUTDIR     (a database backup)
  python scripts/restore_backup.py files/journal-media/x/y.jpg.enc OUTFILE   (a stored file)

Needs BACKUP_ENCRYPTION_KEY. Database backups unpack to one <table>.jsonl per
table — re-importing is deliberately manual (insert in FK order: properties,
units, line_items, then the rest) so a restore is never run by accident.
"""

import io
import os
import sys
import tarfile
from pathlib import Path

from cryptography.fernet import Fernet


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    data = Fernet(os.environ["BACKUP_ENCRYPTION_KEY"]).decrypt(source.read_bytes())
    if source.name.endswith(".tar.gz.enc"):
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(target, filter="data")
        print(f"unpacked to {target}: {sorted(p.name for p in target.iterdir())}")
    else:
        target.write_bytes(data)
        print(f"wrote {target} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
