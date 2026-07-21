STATUS_COLORS = {
    "completed": "#10b981",  # emerald-500
    "complete": "#10b981",  # emerald-500
    "in progress": "#f59e0b",  # amber-500
    "pending": "#a1a1aa",  # zinc-400
}


def status_color(status: str | None) -> str:
    return STATUS_COLORS.get((status or "pending").strip().lower(), "#a1a1aa")


def normalize_status(status: str | None) -> str:
    lowered = (status or "pending").strip().lower()
    if lowered in ("completed", "complete"):
        return "Completed"
    if lowered == "in progress":
        return "In Progress"
    return "Pending"
