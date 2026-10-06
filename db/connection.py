from functools import lru_cache

from supabase import Client, create_client

from utils.settings import get_setting


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL / SUPABASE_KEY. Copy .env.example to .env "
            "and fill in your project credentials."
        )
    return create_client(url, key)
