"""Render target for the hidden /go and /demo pages (see utils/pages.py).

Renders Dashboard's content directly — deliberately NOT via
st.switch_page. switch_page was tried first, but it clears
st.query_params on navigation, wiping out the token main.py had just
resolved and written there a moment earlier: the URL would end up back
at "/" with no token at all, which is what was actually causing "Access
Denied" after saving to the home screen (a real, confirmed bug — not the
iOS canonical-URL theory this started from). Rendering in place avoids
triggering that extra navigation/rerun entirely.
"""

from views import dashboard


def render():
    dashboard.render()
