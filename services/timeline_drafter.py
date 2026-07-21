import json
from datetime import date

from utils.anthropic_client import get_anthropic_client, get_model

DRAFT_TOOL = {
    "name": "record_draft_timeline",
    "description": (
        "Record a sequenced draft construction timeline derived from rough notes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_name": {"type": "string"},
                        "cost_group": {
                            "type": "string",
                            "description": (
                                "Trade category, e.g. Demo, Structural, Framing, "
                                "Roofing, Plumbing, Electrical, HVAC, Drywall, "
                                "Flooring, Paint, Fixtures, Exterior, Inspection, "
                                "Cleanup, Contingency."
                            ),
                        },
                        "duration_days": {"type": "integer"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "task_name(s) of prerequisite tasks that must "
                                "finish before this one starts. Must exactly "
                                "match another task_name in this same list, or "
                                "be empty."
                            ),
                        },
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "estimated_end_date": {
                            "type": "string",
                            "description": "YYYY-MM-DD",
                        },
                        "renamed_from": {
                            "type": "string",
                            "description": (
                                "Only set this when refining an EXISTING schedule "
                                "and this task is a renamed/reworded continuation "
                                "of one specific existing task (not a new task) — "
                                "set it to that task's exact previous task_name so "
                                "its tracked status can be preserved. Leave unset "
                                "for genuinely new tasks, including both halves of "
                                "a split or the result of a merge — a split/merge "
                                "has no single prior task to preserve identity "
                                "from. Not applicable when there is no existing "
                                "schedule yet (initial draft generation)."
                            ),
                        },
                    },
                    "required": [
                        "task_name",
                        "duration_days",
                        "start_date",
                        "estimated_end_date",
                    ],
                },
            },
        },
        "required": ["tasks"],
    },
}

DRAFT_SYSTEM_PROMPT = """You are a construction project management assistant helping \
a property manager turn rough contractor notes into a sequenced draft construction \
timeline for a single property named "{property_name}".

The notes are informal — bullet points, fragments, or rough recollections from a \
contractor conversation, not a structured spreadsheet. Your job:

1. Extract every distinct task/scope item mentioned or clearly implied.
2. Assign each a cost_group using standard residential renovation trade categories \
(Demo, Structural, Framing, Roofing, Plumbing, Electrical, HVAC, Drywall, Flooring, \
Paint, Fixtures, Exterior, Inspection, Cleanup, Contingency).
3. Sequence tasks in standard construction order of operations — demo/permits \
first, then structural/framing, then rough-in trades, then drywall, then finishes, \
then punch list/cleanup last.
4. For each task, list depends_on: the task_name(s) of any other task in this list \
that must genuinely finish first (empty list if none). Use this to capture real \
prerequisite relationships (e.g. drywall depends on rough-in plumbing/electrical in \
the same area), not just chronological ordering.
5. Give each task a realistic duration_days for its scope, and compute start_date / \
estimated_end_date by chaining tasks from a baseline start of {baseline_start}, \
respecting the depends_on relationships — a task cannot start before all of its \
dependencies have finished.

Call the record_draft_timeline tool with the complete result. Do not include any \
commentary outside of the tool call."""

REFINE_SYSTEM_PROMPT = """You are refining an existing draft construction timeline \
for "{property_name}" based on a change request. You will be given the CURRENT \
draft tasks (as JSON) and a natural-language instruction describing what to change.

Apply the requested change while preserving everything else about the draft that \
wasn't asked to change. Maintain consistent sequencing: if the change shifts a \
task's timing, cascade that shift through any tasks that depend on it (directly or \
transitively), using the same depends_on relationships. Re-derive start_date and \
estimated_end_date for every task so the whole timeline remains internally \
consistent after the change.

If you rename or reword an existing task without splitting or merging it, set its \
renamed_from field to the exact previous task_name it replaces — this lets tracked \
progress (e.g. a task already marked in-progress or complete) survive the rename \
instead of resetting. Do NOT set renamed_from on a split's resulting tasks or on \
the merged result of combining tasks — those are genuinely new tasks with no \
single prior task to inherit identity from.

Call the record_draft_timeline tool with the complete UPDATED result (all tasks, \
not just the changed ones). Do not include any commentary outside of the tool \
call."""


def draft_timeline_from_notes(
    property_name: str, notes: str, baseline_start: date
) -> list[dict]:
    """Generate an initial sequenced draft timeline from rough free-text notes."""
    client = get_anthropic_client()
    message = client.messages.create(
        model=get_model(),
        max_tokens=4000,
        system=DRAFT_SYSTEM_PROMPT.format(
            property_name=property_name, baseline_start=baseline_start.isoformat()
        ),
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "record_draft_timeline"},
        messages=[{"role": "user", "content": f"Rough notes:\n\n{notes}"}],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input["tasks"]


def refine_timeline_draft(
    property_name: str, current_tasks: list[dict], instruction: str
) -> list[dict]:
    """Apply a natural-language tweak to an existing draft, returning the full
    updated task list (re-sequenced/re-dated as needed)."""
    client = get_anthropic_client()
    message = client.messages.create(
        model=get_model(),
        max_tokens=4000,
        system=REFINE_SYSTEM_PROMPT.format(property_name=property_name),
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "record_draft_timeline"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Current draft tasks (JSON):\n{json.dumps(current_tasks)}\n\n"
                    f"Requested change:\n{instruction}"
                ),
            }
        ],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input["tasks"]


def _task_label(item: dict, unit_id_to_name: dict | None) -> str:
    """'Unit Name: Task Name' when unit context is available, else just the
    task name. Prefixing is load-bearing, not cosmetic: the same task_name
    can legitimately appear in two different units of the same property
    (verified in real data), and depends_on/matching is name-keyed — without
    the unit prefix, two same-named tasks in different units would collide
    and silently resolve to the wrong row."""
    if not unit_id_to_name:
        return item["task_name"]
    unit_name = unit_id_to_name.get(item["unit_id"])
    return f"{unit_name}: {item['task_name']}" if unit_name else item["task_name"]


def active_tasks_from_line_items(
    items: list[dict], unit_id_to_name: dict | None = None
) -> list[dict]:
    """Convert real line_items rows (with DB-generated ids and UUID
    dependencies) into the flat task-dict shape the drafting/refinement
    functions expect (dependencies expressed as label references,
    duration_days derived from the date range) — the AI doesn't know or
    need to know database IDs.

    Pass unit_id_to_name when items span more than one unit, so labels are
    disambiguated by unit (see _task_label) — apply_schedule_adjustments
    must be given the same unit_id_to_name to match back correctly.
    """
    id_to_label = {item["id"]: _task_label(item, unit_id_to_name) for item in items}
    tasks = []
    for item in items:
        start = item.get("start_date")
        end = item.get("estimated_end_date")
        duration_days = 1
        if start and end:
            duration_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        depends_on = [
            id_to_label[dep_id]
            for dep_id in (item.get("dependencies") or [])
            if dep_id in id_to_label
        ]
        tasks.append(
            {
                "task_name": _task_label(item, unit_id_to_name),
                "cost_group": item.get("cost_group"),
                "duration_days": duration_days,
                "depends_on": depends_on,
                "start_date": start,
                "estimated_end_date": end,
            }
        )
    return tasks


def tasks_to_markdown_table(tasks: list[dict]) -> str:
    """Renders chronologically by start_date, not the order tasks happen to
    arrive in (the AI's own task order isn't guaranteed to be date-order,
    especially after a refinement that reshuffles a few dates)."""
    ordered = sorted(tasks, key=lambda t: t.get("start_date") or "")
    header = "| Task | Trade | Duration | Depends On | Start | End |\n"
    header += "|---|---|---|---|---|---|\n"
    rows = []
    for t in ordered:
        depends = ", ".join(t.get("depends_on") or []) or "—"
        rows.append(
            f"| {t['task_name']} | {t.get('cost_group') or '—'} | "
            f"{t.get('duration_days', '—')}d | {depends} | "
            f"{t.get('start_date', '—')} | {t.get('estimated_end_date', '—')} |"
        )
    return header + "\n".join(rows)
