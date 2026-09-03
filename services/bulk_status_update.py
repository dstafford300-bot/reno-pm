"""Bulk schedule status updates from a direct message to Jeeves — e.g.
"the whole roof is complete at 809" marks every roof-related line item
Completed. DM-only (never group chats), and always preview-then-confirm:
the first message proposes a match and stores it in pending_bulk_updates;
only a subsequent "yes" actually writes anything. This exists because
percent_complete drives draw-milestone eligibility on the Budget page —
a bad match silently completing the wrong tasks has real financial
consequences, so nothing gets written without an explicit confirmation
that shows exactly what matched first.

Deliberately not using Streamlit (utils.anthropic_client/utils.settings)
— this module runs inside the standalone webhook process (webhook_main.py).
"""

import os

from anthropic import Anthropic
from supabase import Client

from services.db_writer import log_activity
from services.receipt_parser import match_property_from_text
from services.telegram_bot import send_and_pin_batch_schedule_update

CONFIRM_WORDS = {"yes", "y", "confirm", "confirmed", "do it", "go ahead", "yep", "yup"}


def _anthropic_client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5"


def is_confirmation(text: str) -> bool:
    return (text or "").strip().lower() in CONFIRM_WORDS


PARSE_REQUEST_TOOL = {
    "name": "record_bulk_status_update",
    "description": (
        "Extract a bulk task-completion update instruction from a direct "
        "message to Jeeves, e.g. 'the whole roof is complete at 809' or "
        "'framing in unit 2 is 50% done at Lincoln Heights'. If the "
        "message is NOT clearly an instruction to change task completion "
        "status (e.g. it's a question, small talk, or something else "
        "entirely), set applicable to false and leave the other fields "
        "null — don't guess."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "applicable": {"type": "boolean"},
            "property_query": {
                "type": ["string", "null"],
                "description": "The property name/address/number mentioned, e.g. '809'.",
            },
            "scope_description": {
                "type": ["string", "null"],
                "description": (
                    "What the update applies to, in the sender's own words, "
                    "e.g. 'the roof', 'framing in unit 2', 'flooring "
                    "throughout'."
                ),
            },
            "status": {
                "type": ["string", "null"],
                "enum": ["Pending", "In Progress", "Completed", None],
                "description": (
                    "Inferred from phrasing: 'complete'/'done'/'finished' "
                    "-> Completed. 'X% done'/'halfway'/'started' -> In "
                    "Progress. 'not started'/'reset' -> Pending."
                ),
            },
            "percent": {
                "type": ["number", "null"],
                "description": (
                    "0-100. 100 for Completed, 0 for Pending, the stated "
                    "percentage for In Progress."
                ),
            },
        },
        "required": ["applicable"],
    },
}

PARSE_SYSTEM_PROMPT = """You are Jeeves, reading a direct message from the property \
manager to see if it's an instruction to update task completion status/percentage \
on the schedule.

Call record_bulk_status_update with the result — applicable=false if this message \
isn't clearly that kind of instruction."""


def _parse_request(text: str) -> dict:
    client = _anthropic_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=512,
        system=PARSE_SYSTEM_PROMPT,
        tools=[PARSE_REQUEST_TOOL],
        tool_choice={"type": "tool", "name": "record_bulk_status_update"},
        messages=[{"role": "user", "content": text}],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input


MATCH_SCOPE_TOOL = {
    "name": "record_matched_tasks",
    "description": (
        "From a numbered list of candidate schedule tasks, return the "
        "indices of every task that is clearly part of the described "
        "scope. Be reasonably inclusive of tasks that are obviously part "
        "of that scope, but don't include tasks that are only loosely or "
        "ambiguously related."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "matched_indices": {
                "type": "array",
                "items": {"type": "integer"},
            }
        },
        "required": ["matched_indices"],
    },
}

MATCH_SYSTEM_PROMPT = """You are Jeeves, matching a described scope of work (e.g. \
"the roof", "framing in unit 2") against a property's actual list of schedule \
tasks, to figure out which specific tasks it refers to.

Call record_matched_tasks with the indices of every task that clearly falls within \
that scope. Do not include commentary outside of the tool call."""


def _match_scope(scope_description: str, candidates: list[dict]) -> list[dict]:
    """candidates: [{"id", "unit_name", "task_name", "cost_group"}, ...].
    Returns the subset that match, via one Claude call over the whole list
    rather than one call per task."""
    if not candidates:
        return []
    lines = "\n".join(
        f"{i}. {c['unit_name']}: {c['task_name']} (cost group: {c.get('cost_group') or 'Uncategorized'})"
        for i, c in enumerate(candidates)
    )
    client = _anthropic_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=1024,
        system=MATCH_SYSTEM_PROMPT,
        tools=[MATCH_SCOPE_TOOL],
        tool_choice={"type": "tool", "name": "record_matched_tasks"},
        messages=[
            {
                "role": "user",
                "content": f"Scope: {scope_description}\n\nCandidate tasks:\n{lines}",
            }
        ],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    indices = set(tool_use.input.get("matched_indices", []))
    return [c for i, c in enumerate(candidates) if i in indices]


def propose_bulk_update(supabase: Client, chat_id: str, text: str) -> str | None:
    """Parses a DM as a potential bulk status-update instruction, matches
    it against the property's real tasks, and — if anything matched —
    stores the proposal and returns a preview reply asking for
    confirmation. Returns None if the message isn't a status-update
    instruction at all (so the caller sends no reply and doesn't spam
    ordinary DM chatter). Never raises."""
    try:
        parsed = _parse_request(text)
    except Exception:
        return None
    if not parsed.get("applicable"):
        return None

    status = parsed.get("status")
    if not status:
        return None
    percent = parsed.get("percent")
    if percent is None:
        percent = {"Completed": 100, "Pending": 0}.get(status, 0)

    properties = (
        supabase.table("properties").select("id, property_name").execute().data
    )
    property_id = match_property_from_text(parsed.get("property_query") or "", properties)
    if not property_id:
        return (
            "I couldn't tell which property you meant — could you include "
            "the address or a distinctive part of it?"
        )
    property_name = next(
        p["property_name"] for p in properties if p["id"] == property_id
    )

    units = (
        supabase.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .execute()
        .data
    )
    unit_ids = [u["id"] for u in units]
    unit_name_by_id = {u["id"]: u["unit_name"] for u in units}
    if not unit_ids:
        return f"{property_name} has no units/tasks yet."

    items = (
        supabase.table("line_items")
        .select("id, unit_id, task_name, cost_group")
        .in_("unit_id", unit_ids)
        .execute()
        .data
    )
    candidates = [
        {
            "id": item["id"],
            "unit_name": unit_name_by_id.get(item["unit_id"], "Unknown unit"),
            "task_name": item["task_name"],
            "cost_group": item.get("cost_group"),
        }
        for item in items
    ]

    scope_description = parsed.get("scope_description") or ""
    try:
        matched = _match_scope(scope_description, candidates)
    except Exception:
        return "I had trouble matching that against the schedule — could you rephrase?"

    if not matched:
        return f"I couldn't find any tasks matching \"{scope_description}\" at {property_name}."

    lines = "\n".join(f"  • {m['unit_name']}: {m['task_name']}" for m in matched)
    percent_label = f" at {percent:.0f}%" if status == "In Progress" else ""
    summary = (
        f"{len(matched)} task(s) matching \"{scope_description}\" at "
        f"{property_name} -> {status}{percent_label}"
    )

    supabase.table("pending_bulk_updates").upsert(
        {
            "chat_id": chat_id,
            "property_id": property_id,
            "property_name": property_name,
            "line_item_ids": [m["id"] for m in matched],
            "new_status": status,
            "new_percent": percent,
            "summary": summary,
        }
    ).execute()

    return (
        f"I found {len(matched)} task(s) at {property_name} matching "
        f'"{scope_description}":\n{lines}\n\n'
        f"Mark these {status}{percent_label}? Reply \"yes\" to confirm."
    )


def apply_pending_update(supabase: Client, chat_id: str) -> str | None:
    """Applies the pending proposal for this chat, if any, and returns a
    confirmation reply. Returns None if there's nothing pending (so the
    caller can fall through to treating the message as something else —
    a stray "yes" with no proposal shouldn't produce a confusing reply).
    """
    rows = (
        supabase.table("pending_bulk_updates")
        .select("*")
        .eq("chat_id", chat_id)
        .execute()
        .data
    )
    if not rows:
        return None
    pending = rows[0]

    line_item_ids = pending["line_item_ids"]
    new_status = pending["new_status"]
    new_percent = pending["new_percent"]
    property_id = pending["property_id"]
    property_name = pending["property_name"]

    items = (
        supabase.table("line_items")
        .select("id, unit_id, task_name")
        .in_("id", line_item_ids)
        .execute()
        .data
    )
    unit_ids = list({item["unit_id"] for item in items})
    units = (
        supabase.table("units")
        .select("id, unit_name")
        .in_("id", unit_ids)
        .execute()
        .data
    )
    unit_name_by_id = {u["id"]: u["unit_name"] for u in units}

    supabase.table("line_items").update(
        {"status": new_status, "percent_complete": new_percent}
    ).in_("id", line_item_ids).execute()

    changes = [
        f"{unit_name_by_id.get(item['unit_id'], 'Unknown unit')}: {item['task_name']} — now {new_status}"
        for item in items
    ]

    supabase.table("pending_bulk_updates").delete().eq("chat_id", chat_id).execute()

    log_activity(supabase, property_id, "schedule", pending["summary"])

    property_row = (
        supabase.table("properties")
        .select("telegram_chat_id")
        .eq("id", property_id)
        .execute()
        .data
    )
    group_chat_id = property_row[0].get("telegram_chat_id") if property_row else None
    send_and_pin_batch_schedule_update(
        property_name=property_name,
        property_id=property_id,
        changes=changes,
        chat_id=group_chat_id,
    )

    return f"Done — updated {len(items)} task(s) at {property_name}."
