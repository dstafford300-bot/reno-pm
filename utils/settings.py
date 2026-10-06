import os

from dotenv import load_dotenv

load_dotenv()


def get_setting(key: str) -> str | None:
    """Read a setting from the environment, falling back to Streamlit
    secrets.toml (used for Streamlit Community Cloud deployments). Streamlit
    is optional: the API (api/) and webhook run without it."""
    value = os.environ.get(key)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets[key]
    except Exception:
        return None
