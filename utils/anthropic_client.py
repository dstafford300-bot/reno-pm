import streamlit as st
from anthropic import Anthropic

from utils.settings import get_setting

DEFAULT_MODEL = "claude-sonnet-5"


@st.cache_resource
def get_anthropic_client() -> Anthropic:
    api_key = get_setting("ANTHROPIC_API_KEY")
    if not api_key:
        st.error(
            "Missing ANTHROPIC_API_KEY. Add it to your .env file to use AI features."
        )
        st.stop()
    return Anthropic(api_key=api_key)


def get_model() -> str:
    return get_setting("ANTHROPIC_MODEL") or DEFAULT_MODEL
