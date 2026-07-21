import hmac
from urllib.parse import quote

import streamlit as st

from utils.pages import PAGES
from utils.settings import get_setting

st.set_page_config(
    page_title="Reno PM",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="auto",
)

HIDE_SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="stSidebarCollapseButton"] { display: none; }
</style>
"""


def _access_denied():
    """No sidebar, no nav, no data reads of any kind — this branch never
    touches Supabase, so a bad/missing token can't leak anything."""
    st.markdown(HIDE_SIDEBAR_CSS, unsafe_allow_html=True)
    st.title("🔒 Access Denied")
    st.write("This link is invalid or has expired. Contact the project manager for access.")
    st.stop()


ACCESS_TOKEN = get_setting("ACCESS_TOKEN")
provided_token = st.query_params.get("token")

# A short, memorable /demo link (no query string at all) is equivalent to
# supplying the demo token directly — resolved into provided_token right
# away so everything below (including the icon rail's link-building,
# which reads provided_token) behaves exactly as if ?token=<demo token>
# had been on the URL from the start.
if not provided_token:
    try:
        from urllib.parse import urlparse

        current_path = urlparse(st.context.url).path.strip("/").lower()
    except Exception:
        current_path = ""
    if current_path == "demo":
        try:
            from db.connection import get_supabase_client
            from services.demo_access import get_demo_token

            provided_token = get_demo_token(get_supabase_client())
        except Exception:
            provided_token = None
        # "/demo" isn't a registered page path, so Streamlit briefly
        # flashes its own "Page not found" toast before falling back to
        # the default (Dashboard) page — tried redirecting past it with
        # st.switch_page, but calling it this early (before
        # st.navigation().run() has established a navigation context for
        # this run) sent the app into a repeating "not found" loop
        # instead. The toast is a cosmetic wrinkle, not a broken link —
        # left as-is rather than fighting Streamlit's router further.

# Constant-time comparison — a plain `!=` leaks how many leading
# characters matched via response-time differences, which matters for a
# token gate like this one.
token_valid = bool(ACCESS_TOKEN) and bool(provided_token) and hmac.compare_digest(
    provided_token, ACCESS_TOKEN
)

if not token_valid and provided_token:
    # Fallback: a separate, independently revocable demo token for
    # sharing with trusted people without touching ACCESS_TOKEN (which
    # Jeeves' Telegram links and your own daily use depend on). Stored in
    # Supabase rather than secrets so it can be revoked instantly by
    # deleting one row. Only reached when the primary token didn't
    # match, and only ever queries this one bot_state row — never
    # property/business data — so an invalid token still can't reach
    # anything sensitive.
    try:
        from db.connection import get_supabase_client
        from services.demo_access import get_demo_token

        demo_token = get_demo_token(get_supabase_client())
    except Exception:
        demo_token = None
    token_valid = bool(demo_token) and hmac.compare_digest(provided_token, demo_token)

if not token_valid:
    _access_denied()

is_client_view = st.query_params.get("view") == "client"

if is_client_view:
    st.markdown(HIDE_SIDEBAR_CSS, unsafe_allow_html=True)
    from views import client_view

    client_view.render(st.query_params.get("property_id"))
    st.stop()

# The icon rail below is raw HTML, not Streamlit's own routed nav — a
# plain <a href="/schedule"> triggers a real browser navigation and would
# silently drop ?token=..., bouncing an authenticated user back to Access
# Denied on the very next click. Every link carries the token explicitly.
_token_qs = f"?token={quote(provided_token)}"

st.markdown(
    """
    <style>
    div.block-container{padding-top:2rem; padding-left:64px;}

    /* Persistent icon rail: a slim, always-visible strip of page icons
    running the full height of the viewport (top:0 — no gap above it),
    independent of Streamlit's own sidebar collapse state.

    Icons-only (48px) below the mobile breakpoint; widens to show text
    labels next to each icon above it — see the min-width media query.
    On desktop, a checkbox-driven CSS toggle (no JS — Streamlit strips
    <script> tags injected via st.markdown) lets you collapse the labels
    back down to icons-only on demand. */
    #jeeves-icon-rail {
        position: fixed;
        top: 0;
        left: 0;
        height: 100vh;
        width: 48px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        padding-top: 60px;
        background: var(--secondary-background-color, #F1F5F9);
        border-right: 1px solid rgba(0,0,0,0.08);
        z-index: 999;
        overflow: hidden;
    }
    #jeeves-icon-rail a {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        width: 36px;
        height: 36px;
        border-radius: 8px;
        font-size: 1.2rem;
        text-decoration: none;
        color: inherit;
        white-space: nowrap;
    }
    #jeeves-icon-rail a:hover {
        background: rgba(0,0,0,0.08);
    }
    #jeeves-icon-rail .rail-label {
        display: none;
        font-size: 0.9rem;
        font-family: "Source Sans Pro", sans-serif;
    }
    #jeeves-rail-toggle { display: none; }
    .jeeves-rail-toggle-label {
        display: none;
        align-items: center;
        justify-content: center;
        width: 36px;
        height: 36px;
        border-radius: 8px;
        cursor: pointer;
        font-size: 1.2rem;
        color: inherit;
        margin-bottom: 8px;
    }
    .jeeves-rail-toggle-label:hover { background: rgba(0,0,0,0.08); }

    @media (min-width: 641px) {
        div.block-container:has(#jeeves-rail-toggle:checked){padding-left:64px;}
        div.block-container:not(:has(#jeeves-rail-toggle:checked)){padding-left:210px;}

        #jeeves-icon-rail { width: 190px; align-items: stretch; padding-top: 60px; }
        #jeeves-icon-rail a {
            justify-content: flex-start;
            width: auto;
            padding: 0 14px;
        }
        #jeeves-icon-rail .rail-label { display: inline; }
        .jeeves-rail-toggle-label { display: flex; justify-content: flex-start; padding: 0 14px; width: auto; }

        div.block-container:has(#jeeves-rail-toggle:checked) #jeeves-icon-rail {
            width: 48px;
        }
        div.block-container:has(#jeeves-rail-toggle:checked) #jeeves-icon-rail a {
            justify-content: center;
            width: 36px;
            padding: 0;
        }
        div.block-container:has(#jeeves-rail-toggle:checked) .rail-label {
            display: none;
        }
        div.block-container:has(#jeeves-rail-toggle:checked) .jeeves-rail-toggle-label {
            justify-content: center;
            width: 36px;
            padding: 0;
        }
    }
    </style>
    <input type="checkbox" id="jeeves-rail-toggle">
    <div id="jeeves-icon-rail">
        <label for="jeeves-rail-toggle" class="jeeves-rail-toggle-label" title="Toggle menu labels">☰</label>
        <a href="/__TOKEN_QS__" title="Dashboard" target="_self">🏠<span class="rail-label">Dashboard</span></a>
        <a href="/schedule__TOKEN_QS__" title="Schedule" target="_self">📅<span class="rail-label">Schedule</span></a>
        <a href="/budget__TOKEN_QS__" title="Budget" target="_self">💰<span class="rail-label">Budget</span></a>
        <a href="/journal__TOKEN_QS__" title="Journal" target="_self">📓<span class="rail-label">Journal</span></a>
        <a href="/upload-sow__TOKEN_QS__" title="Upload SOW" target="_self">📤<span class="rail-label">Upload SOW</span></a>
        <a href="/material-logs__TOKEN_QS__" title="Material Logs" target="_self">🧾<span class="rail-label">Material Logs</span></a>
    </div>
    """.replace("__TOKEN_QS__", _token_qs),
    unsafe_allow_html=True,
)

# position="hidden": Streamlit's own sidebar nav links do an internal
# navigation that was found (live-tested) to drop st.query_params entirely,
# which would silently strip ?token=... and bounce an authenticated user
# to Access Denied on click. The custom icon rail above is the only nav
# surface — its links explicitly carry the token — so the native sidebar
# page list is turned off rather than left as a trap.
st.markdown(HIDE_SIDEBAR_CSS, unsafe_allow_html=True)
pg = st.navigation(PAGES, position="hidden")
pg.run()
