import streamlit as st
from supabase import Client, create_client

from utils.settings import get_setting


@st.cache_resource
def get_supabase_client() -> Client:
    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_KEY")

    if not url or not key:
        st.error(
            "Missing SUPABASE_URL / SUPABASE_KEY. Copy .env.example to .env "
            "and fill in your project credentials."
        )
        st.stop()

    return create_client(url, key)
