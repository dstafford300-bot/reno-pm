"""A second, independently revocable access token — for sharing a demo
link with trusted people without touching the primary ACCESS_TOKEN
everyone else (including Jeeves' Telegram links) relies on daily.

Stored in bot_state (Supabase) rather than st.secrets, specifically so it
can be revoked instantly by deleting one row — no redeploy needed.
"""

from supabase import Client

_DEMO_TOKEN_KEY = "demo_access_token"


def get_demo_token(client: Client) -> str | None:
    rows = (
        client.table("bot_state")
        .select("value")
        .eq("key", _DEMO_TOKEN_KEY)
        .execute()
        .data
    )
    return rows[0]["value"] if rows else None


def set_demo_token(client: Client, token: str) -> None:
    client.table("bot_state").upsert(
        {"key": _DEMO_TOKEN_KEY, "value": token}
    ).execute()


def clear_demo_token(client: Client) -> None:
    client.table("bot_state").delete().eq("key", _DEMO_TOKEN_KEY).execute()
