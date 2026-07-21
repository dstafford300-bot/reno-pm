import html

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.mobile import inject_mobile_gantt_fallback_css, sanitize_key
from utils.status import normalize_status, status_color

# Derived from utils.status so the chart never drifts from the pill colors
# used in the Timeline Feed and Dashboard.
STATUS_COLOR_MAP = {
    "Completed": status_color("Completed"),
    "In Progress": status_color("In Progress"),
    "Pending": status_color("Pending"),
}

APP_BACKGROUND = "#FFFFFF"  # must match .streamlit/config.toml backgroundColor
CHART_TEXT_COLOR = "#1E293B"  # must match .streamlit/config.toml textColor
GRIDLINE_COLOR = "rgba(0,0,0,0.12)"


def prepare_gantt_dataframe(
    items: list[dict], unit_id_to_name: dict | None = None, label_with_unit: bool = False
):
    """items: line_items rows with at least id, unit_id, task_name, status,
    start_date, estimated_end_date.

    Returns (df, chronological_order, skipped_count) — df is filtered to
    valid dates only, sorted by start_date, with status_norm and label
    columns added. df is empty (with chronological_order == []) if nothing
    has valid dates.
    """
    df = pd.DataFrame(items)
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["estimated_end_date"] = pd.to_datetime(
        df["estimated_end_date"], errors="coerce"
    )

    missing = df["start_date"].isna() | df["estimated_end_date"].isna()
    skipped_count = int(missing.sum())
    df = df[~missing].copy()

    if df.empty:
        return df, [], skipped_count

    df["status_norm"] = df["status"].apply(normalize_status)
    if label_with_unit and unit_id_to_name:
        df["unit_name"] = df["unit_id"].map(unit_id_to_name)
        df["label"] = df["unit_name"] + ": " + df["task_name"]
    else:
        df["label"] = df["task_name"]
    df = df.sort_values("start_date")
    chronological_order = df["label"].tolist()
    return df, chronological_order, skipped_count


def build_gantt_figure(df, chronological_order: list[str]):
    df = df.copy()
    df["start_label"] = df["start_date"].dt.strftime("%b %-d, %Y")
    df["end_label"] = df["estimated_end_date"].dt.strftime("%b %-d, %Y")

    fig = px.timeline(
        df,
        x_start="start_date",
        x_end="estimated_end_date",
        y="label",
        color="status_norm",
        color_discrete_map=STATUS_COLOR_MAP,
        category_orders={"label": chronological_order},
        custom_data=["id", "start_label", "end_label", "status_norm"],
    )
    # NOTE: intentionally NOT setting autorange="reversed" here — with
    # px.timeline + explicit category_orders, the default axis order already
    # renders top-down chronologically (earliest at top). Adding the
    # reversal flips that into bottom-up (verified by rendering both
    # variants to PNG in an earlier session).
    fig.update_yaxes(title=None, showgrid=False, zeroline=False)

    # Hard pan/zoom boundary (not just an initial view) — without this,
    # dragging the chart on mobile scrolls the date axis indefinitely in
    # both directions with nothing but empty space past the real tasks.
    # Anchored to the project's own start date, not "today", so the
    # window stays put regardless of when someone happens to view it.
    project_start = df["start_date"].min()
    min_allowed = (project_start - pd.Timedelta(days=14)).to_pydatetime()
    max_allowed = (project_start + pd.DateOffset(months=6)).to_pydatetime()

    fig.update_xaxes(
        title=None,
        showgrid=True,
        gridcolor=GRIDLINE_COLOR,
        gridwidth=1,
        zeroline=False,
        minallowed=min_allowed,
        maxallowed=max_allowed,
    )

    # Mirror the date axis onto a second, invisible-trace axis pinned to the
    # top of the chart — Plotly only draws tick labels on an axis that has
    # at least one trace referencing it, so a fully transparent marker
    # trace is added purely to make the top axis render.
    fig.add_trace(
        go.Scatter(
            x=[
                df["start_date"].min().to_pydatetime(),
                df["estimated_end_date"].max().to_pydatetime(),
            ],
            y=[chronological_order[0]] * 2,
            mode="markers",
            marker=dict(opacity=0),
            showlegend=False,
            hoverinfo="skip",
            xaxis="x2",
        )
    )
    fig.update_layout(
        xaxis2=dict(
            matches="x",
            overlaying="x",
            side="top",
            showgrid=False,
            zeroline=False,
            showticklabels=True,
        )
    )

    fig.update_traces(
        selector=dict(type="bar"),
        marker_cornerradius=8,
        marker_line_width=1,
        marker_line_color="rgba(0,0,0,0.15)",
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Status: %{customdata[3]}<br>"
            "%{customdata[1]} → %{customdata[2]}"
            "<extra></extra>"
        ),
    )
    fig.update_layout(
        height=max(320, 42 * len(chronological_order)),
        margin=dict(l=10, r=10, t=40, b=30),
        paper_bgcolor=APP_BACKGROUND,
        plot_bgcolor=APP_BACKGROUND,
        font=dict(family="Inter, Arial, sans-serif", color=CHART_TEXT_COLOR, size=13),
        legend_title_text="Status",
        bargap=0.35,
    )
    return fig


def render_responsive_gantt_chart(
    df, chronological_order: list[str], key_prefix: str, show_chart: bool = True
):
    """Renders the Gantt chart plus its mobile linear-list fallback.

    The chart/list split is decided by CSS using the device's actual
    *orientation*, not just viewport width — a phone rotated to
    landscape is still often narrower than a small tablet's portrait
    width, so a plain width breakpoint left rotation doing nothing. Below
    the breakpoint AND in portrait: list only. Landscape (regardless of
    width) or above the breakpoint: chart only. See utils/mobile.py.

    `show_chart=False` (the Schedule page's own checkbox) skips building
    the chart entirely and forces the list on at every viewport/
    orientation — unchecking it always means "just show me the list."

    Shared by the Schedule page (which uses the returned click event to
    drive its quick-edit overlay) and the read-only client view (which
    just discards it). Returns the plotly_chart `event` object, or None
    if df is empty or show_chart is False.
    """
    if df.empty:
        st.info("None of the tasks in this selection have valid dates yet.")
        return None

    key_prefix = sanitize_key(key_prefix)
    chart_key = f"{key_prefix}_gantt_wrapper"
    list_key = f"{key_prefix}_mobile_task_list"

    event = None
    if show_chart:
        inject_mobile_gantt_fallback_css(chart_key=chart_key, list_key=list_key)
        with st.container(key=chart_key):
            fig = build_gantt_figure(df, chronological_order)
            event = st.plotly_chart(
                fig,
                use_container_width=True,
                config={"displayModeBar": False},
                on_select="rerun",
                selection_mode="points",
                key=f"{key_prefix}_gantt_chart",
            )
    else:
        # No chart in the DOM at all — force the list on unconditionally,
        # overriding the default-hidden rule inject_mobile_gantt_fallback_css
        # would otherwise apply (that CSS never even runs in this branch).
        st.markdown(
            f'<style>[class*="st-key-{list_key}"] {{ display: block !important; }}</style>',
            unsafe_allow_html=True,
        )

    with st.container(key=list_key):
        if show_chart:
            st.info("🔄 Rotate to landscape to view the full Gantt Chart.")
        mobile_df = df.sort_values("start_date")
        phases: dict[str, list] = {}
        for _, mobile_row in mobile_df.iterrows():
            phase_name = mobile_row.get("cost_group") or "Uncategorized"
            phases.setdefault(phase_name, []).append(mobile_row)

        for phase_name, phase_rows in phases.items():
            # Collapsed by default — headings only until tapped, so a
            # long task list doesn't turn into a wall of open cards.
            with st.expander(html.escape(phase_name), expanded=False):
                for mobile_row in phase_rows:
                    percent = mobile_row.get("percent_complete") or 0
                    status = mobile_row["status_norm"]
                    color = status_color(status)
                    start_str = mobile_row["start_date"].strftime("%m-%d-%Y")
                    end_str = mobile_row["estimated_end_date"].strftime("%m-%d-%Y")
                    st.markdown(
                        f"""
                        <div style="border:1px solid rgba(0,0,0,0.12);border-radius:10px;
                                    padding:0.75rem 1rem;margin-bottom:0.6rem;">
                            <div style="font-weight:600;margin-bottom:0.35rem;">
                                {html.escape(mobile_row['label'])}
                            </div>
                            <div style="display:flex;justify-content:space-between;
                                        align-items:center;flex-wrap:wrap;gap:0.5rem;">
                                <span style="background:{color};color:white;padding:2px 10px;
                                            border-radius:999px;font-size:0.75rem;
                                            font-weight:600;">{percent:.0f}% — {html.escape(status)}</span>
                                <span style="font-size:0.8rem;opacity:0.7;">
                                    {start_str} → {end_str}
                                </span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

    return event
