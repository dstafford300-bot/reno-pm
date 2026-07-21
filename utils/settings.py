import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def get_setting(key: str) -> str | None:
    """Read a setting from the environment, falling back to Streamlit
    secrets.toml (used for Streamlit Community Cloud deployments)."""
    value = os.environ.get(key)
    if value:
        return value
    try:
        return st.secrets[key]
    except (FileNotFoundError, KeyError):
        return None
