from functools import lru_cache

from anthropic import Anthropic

from utils.settings import get_setting

DEFAULT_MODEL = "claude-sonnet-5"


@lru_cache(maxsize=1)
def get_anthropic_client() -> Anthropic:
    api_key = get_setting("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY. Add it to your .env file to use AI features."
        )
    return Anthropic(api_key=api_key)


def get_model() -> str:
    return get_setting("ANTHROPIC_MODEL") or DEFAULT_MODEL
