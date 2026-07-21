import re
from datetime import date, timedelta

# Standard construction order of operations. Cost groups are matched
# case-insensitively; anything unrecognized falls back to _DEFAULT_RANK so it
# doesn't break the ordering of everything after it.
PHASE_ORDER = [
    "demo",
    "structural",
    "framing",
    "roofing",
    "plumbing",
    "electrical",
    "hvac",
    "drywall",
    "flooring",
    "paint",
    "fixtures",
    "exterior",
    "inspection",
    "cleanup",
    "contingency",
]
_PHASE_RANK = {name: rank for rank, name in enumerate(PHASE_ORDER)}
_DEFAULT_RANK = len(PHASE_ORDER) // 2

# Baseline duration (in days) per trade, before the light budget-size
# adjustment in _duration_days.
_BASE_DURATION_DAYS = {
    "demo": 2,
    "structural": 3,
    "framing": 4,
    "roofing": 6,
    "plumbing": 3,
    "electrical": 3,
    "hvac": 2,
    "drywall": 3,
    "flooring": 4,
    "paint": 3,
    "fixtures": 3,
    "exterior": 3,
    "inspection": 1,
    "cleanup": 1,
    "contingency": 0,
}

# --- Unit priority (dry-in envelope first) ---------------------------------

_EXTERIOR_PATTERN = re.compile(r"exterior|\bbuilding\b", re.IGNORECASE)
_BASEMENT_PATTERN = re.compile(r"basement|mechanical", re.IGNORECASE)
_CONTINGENCY_PATTERN = re.compile(r"contingency", re.IGNORECASE)
# Mirrors utils.units' living-unit detection; kept local so the scheduling
# engine doesn't reach into the view-layer helper module.
_LIVING_UNIT_PATTERN = re.compile(
    r"\bunit\s+\w|\bunit\d|\b(apt|apartment|suite|floor)\b", re.IGNORECASE
)


def _unit_category_rank(unit_name: str) -> int:
    """0=Exterior/Building, 1=Basement/Mechanical, 2=other common areas,
    3=Living units, 4=Contingency (always last)."""
    if _CONTINGENCY_PATTERN.search(unit_name):
        return 4
    if _EXTERIOR_PATTERN.search(unit_name):
        return 0
    if _BASEMENT_PATTERN.search(unit_name):
        return 1
    if _LIVING_UNIT_PATTERN.search(unit_name):
        return 3
    return 2


def order_units_by_priority(units: list[dict]) -> list[dict]:
    """Dry-in envelope first: Exterior/Building -> Basement/Mechanical ->
    other common areas -> Living Units (sequentially) -> Contingency last."""
    return sorted(
        units, key=lambda u: (_unit_category_rank(u["unit_name"]), u["unit_name"])
    )


def _phase_rank(cost_group: str | None) -> int:
    return _PHASE_RANK.get((cost_group or "").strip().lower(), _DEFAULT_RANK)


def _duration_days(cost_group: str | None, budgeted_cost) -> int:
    key = (cost_group or "").strip().lower()
    if key == "contingency":
        return 0
    base = _BASE_DURATION_DAYS.get(key, 2)
    # Larger-budget tasks within a trade tend to run longer; nudge duration
    # up for costlier line items without letting it run away.
    budget_bump = round((budgeted_cost or 0) / 1500)
    return max(1, min(base + budget_bump, base + 6))


def build_schedule(
    units: list[dict], line_items: list[dict], baseline_start: date
) -> dict[str, dict]:
    """Phase-parallel (line-of-balance) scheduling.

    Each trade phase is modeled as a single crew that moves through units in
    priority order (see order_units_by_priority). A phase can't start in a
    given unit until BOTH:
      (a) that same trade's crew has finished the previous unit, and
      (b) the prior trade phase has finished in this same unit.

    This lets different trades run concurrently across different units —
    e.g. Framing moves into the Basement right behind Demo, while Demo has
    already moved on to Unit 1 — instead of one unit fully finishing before
    the next one starts.

    Contingency is deliberately excluded from the crew cascade — it's a
    reserve line, not a trade, so nothing else naturally forces it to wait
    its turn. It's pinned to start exactly when every other task finishes.

    units: [{"id": ..., "unit_name": ...}, ...]
    line_items: [{"id": ..., "unit_id": ..., "cost_group": ..., "budgeted_cost": ...}, ...]

    Returns {line_item_id: {"start_date": "YYYY-MM-DD", "estimated_end_date": "YYYY-MM-DD"}}.
    """
    ordered_units = order_units_by_priority(units)
    contingency_unit_ids = {
        u["id"] for u in ordered_units if _unit_category_rank(u["unit_name"]) == 4
    }

    def _is_contingency(item: dict) -> bool:
        return (
            item["unit_id"] in contingency_unit_ids
            or (item.get("cost_group") or "").strip().lower() == "contingency"
        )

    regular_items = [i for i in line_items if not _is_contingency(i)]
    contingency_items = [i for i in line_items if _is_contingency(i)]
    regular_units = [u for u in ordered_units if u["id"] not in contingency_unit_ids]

    items_by_unit_phase: dict[tuple[str, int], list[dict]] = {}
    for item in regular_items:
        phase = _phase_rank(item.get("cost_group"))
        items_by_unit_phase.setdefault((item["unit_id"], phase), []).append(item)

    # Tracks, per unit, when the previously-run phase finished in that unit
    # (same-unit precedence: can't drywall before framing is done there).
    unit_prev_phase_end: dict[str, date] = {
        unit["id"]: baseline_start for unit in regular_units
    }

    schedule: dict[str, dict] = {}

    phases_present = sorted({_phase_rank(i.get("cost_group")) for i in regular_items})

    for phase in phases_present:
        # Tracks when this phase's single crew becomes free again, as it
        # cascades through units in priority order.
        crew_free_at = baseline_start

        for unit in regular_units:
            items = items_by_unit_phase.get((unit["id"], phase))
            if not items:
                continue

            start = max(crew_free_at, unit_prev_phase_end[unit["id"]])
            cursor = start
            for item in items:
                duration = _duration_days(
                    item.get("cost_group"), item.get("budgeted_cost")
                )
                item_start = cursor
                item_end = item_start + timedelta(days=duration)
                schedule[item["id"]] = {
                    "start_date": item_start.isoformat(),
                    "estimated_end_date": item_end.isoformat(),
                }
                cursor = item_end

            crew_free_at = cursor
            unit_prev_phase_end[unit["id"]] = cursor

    project_end = max(
        (date.fromisoformat(v["estimated_end_date"]) for v in schedule.values()),
        default=baseline_start,
    )
    for item in contingency_items:
        schedule[item["id"]] = {
            "start_date": project_end.isoformat(),
            "estimated_end_date": project_end.isoformat(),
        }

    return schedule
