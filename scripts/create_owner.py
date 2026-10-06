"""Create (or reset) the owner account for the app.

Usage:  python scripts/create_owner.py you@example.com "Your Name"
Prints a temporary password (shown once); you're asked to change it at first
sign-in. Run after scripts/migration_app_auth.sql.
"""

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.security import hash_password
from db.connection import get_supabase_client


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    email, name = sys.argv[1].strip().lower(), sys.argv[2].strip()
    db = get_supabase_client()
    password = secrets.token_urlsafe(9)
    existing = db.table("app_users").select("id").eq("email", email).execute().data
    fields = {
        "name": name, "role": "owner", "active": True,
        "password_hash": hash_password(password), "must_change_password": True,
    }
    if existing:
        db.table("app_users").update(fields).eq("id", existing[0]["id"]).execute()
        print(f"Reset owner {email}")
    else:
        db.table("app_users").insert({"email": email, **fields}).execute()
        print(f"Created owner {email}")
    print(f"Temporary password: {password}")


if __name__ == "__main__":
    main()
