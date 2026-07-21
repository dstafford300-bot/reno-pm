import streamlit as st

from views import budget, dashboard, journal, material_logs, schedule, upload_sow

DASHBOARD_PAGE = st.Page(
    dashboard.render, title="Dashboard", icon="🏠", url_path="dashboard", default=True
)
SCHEDULE_PAGE = st.Page(
    schedule.render, title="Schedule", icon="📅", url_path="schedule"
)
BUDGET_PAGE = st.Page(
    budget.render, title="Budget", icon="💰", url_path="budget"
)
JOURNAL_PAGE = st.Page(
    journal.render, title="Journal", icon="📓", url_path="journal"
)
UPLOAD_SOW_PAGE = st.Page(
    upload_sow.render, title="Upload SOW", icon="📤", url_path="upload-sow"
)
MATERIAL_LOGS_PAGE = st.Page(
    material_logs.render,
    title="Material Logs",
    icon="🧾",
    url_path="material-logs",
)

PAGES = [
    DASHBOARD_PAGE,
    SCHEDULE_PAGE,
    BUDGET_PAGE,
    JOURNAL_PAGE,
    UPLOAD_SOW_PAGE,
    MATERIAL_LOGS_PAGE,
]
