"""Mobile field-use CSS helpers.

Viewport detection is done entirely client-side via CSS media queries —
Streamlit reruns don't have direct access to the browser's viewport width
without a custom JS component, so instead of branching in Python, both
layouts are rendered and the media query decides which one is visible.
"""

import re

import streamlit as st

# Covers phones and tablet PORTRAIT widths (iPad is 768px, iPad Pro up to
# 834px) — landscape tablets (1024px+) still get the full interactive
# Gantt chart, matching the fallback's own "rotate screen horizontally"
# tip. Originally 640px (phones only); iPad portrait was falling through
# to the full desktop layout, which is what prompted raising it.
MOBILE_BREAKPOINT_PX = 900


def sanitize_key(value: str) -> str:
    """Streamlit sanitizes st.container(key=...) into a CSS class as
    `st-key-<key>`, but its own sanitization rules for the key's contents
    aren't part of the public API — e.g. spaces become hyphens, not
    underscores. Any key built from dynamic values (a unit name like
    "All Units") has to go through this first, so the Python string we
    build CSS selectors from is guaranteed to match what Streamlit
    actually renders, rather than silently never matching."""
    return re.sub(r"[^a-zA-Z0-9_]+", "-", value)


def inject_mobile_button_css():
    """48px-min-height touch targets for every button, but only on narrow
    viewports — desktop keeps its normal compact button height.

    Full WIDTH can't be done this way — Streamlit's buttons size
    themselves imperatively via JS on every render pass, so even an
    inline `!important` CSS width gets silently overwritten a moment
    later. Callers pass `width="stretch"` directly to st.button() for
    that (Streamlit's own supported mechanism); this helper only handles
    the touch-target height, which CSS *can* control.
    """
    st.markdown(
        f"""
        <style>
        @media (max-width: {MOBILE_BREAKPOINT_PX}px) {{
            div[data-testid="stButton"] button,
            div[data-testid="stDownloadButton"] button {{
                min-height: 48px !important;
                font-size: 1rem !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_mobile_card_css(key_prefixes: list[str]):
    """Adds a border, padding, and bolder labels to st.container(key=...)
    elements whose key starts with one of `key_prefixes`, but only on
    narrow viewports — turns a cramped multi-column row into a clearly
    separated card once columns stack (Streamlit already stacks st.columns
    vertically below this breakpoint on its own)."""
    selectors = ", ".join(f'[class*="st-key-{p}"]' for p in key_prefixes)
    st.markdown(
        f"""
        <style>
        @media (max-width: {MOBILE_BREAKPOINT_PX}px) {{
            {selectors} {{
                border: 1px solid rgba(0,0,0,0.12) !important;
                border-radius: 12px !important;
                padding: 1rem !important;
                margin-bottom: 0.75rem !important;
            }}
            {selectors} strong {{
                font-size: 1.05rem;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_mobile_gantt_fallback_css(chart_key: str, list_key: str):
    """Swaps the Gantt chart for a vertical task list below the mobile
    breakpoint — both are rendered, CSS picks which one is visible so no
    server round-trip is needed to react to viewport size."""
    st.markdown(
        f"""
        <style>
        [class*="st-key-{list_key}"] {{ display: none; }}
        @media (max-width: {MOBILE_BREAKPOINT_PX}px) {{
            [class*="st-key-{chart_key}"] {{ display: none !important; }}
            [class*="st-key-{list_key}"] {{ display: block !important; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
