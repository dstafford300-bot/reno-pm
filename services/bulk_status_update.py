"""Schedule and draw updates from a direct message to Jeeves — one sentence
("the whole roof is complete at 809") or a whole reply to the daily summary:
many tasks at different stages across both properties, in the sender's own
wording, plus draws that were paid. DM-only (never group chats), and always
preview-then-confirm:
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

from services.db_writer import log_activity, release_draw_milestone
from services.telegram_bot import send_and_pin_batch_schedule_update

import html
import re
from datetime import datetime, timedelta, timezone

_YES_FIRST = {"yes", "y", "yep", "yup", "yeah", "yea", "ok", "okay", "sure", "confirm",
              "confirmed", "approve", "approved", "correct", "affirmative"}
_YES_PHRASES = {"do it", "go ahead", "please do", "sounds good", "that's right", "thats right"}
_NO_FIRST = {"no", "nope", "cancel", "stop", "negative", "wrong", "abort"}
_NO_PHRASES = {"never mind", "nevermind", "don't", "dont", "do not"}

# A proposal that sits unanswered must not be applied by a stray "yes" days
# later, against a schedule that has since moved on.
PROPOSAL_TTL = timedelta(hours=12)


def _anthropic_client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5"


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", (text or "").lower()).split()


def _short_reply(text: str, first_words: set[str], phrases: set[str]) -> bool:
    """A short message that opens with one of the words (or is one of the
    phrases): "Yes.", "yes please", "Yep!", "ok". Capped at 4 words so a real
    instruction that merely starts with "yes" ("yes, and the gutters are
    done too") is not mistaken for an answer."""
    words = _words(text)
    if not words or len(words) > 4:
        return False
    return words[0] in first_words or " ".join(words) in phrases


def is_confirmation(text: str) -> bool:
    t = (text or "").strip()
    return _short_reply(t, _YES_FIRST, _YES_PHRASES) or t in ("👍", "✅")


def is_cancellation(text: str) -> bool:
    return _short_reply(text, _NO_FIRST, _NO_PHRASES) or (text or "").strip() == "👎"


def cancel_pending_update(supabase: Client, chat_id: str) -> str | None:
    """Drops the pending proposal. None if there wasn't one."""
    rows = supabase.table("pending_bulk_updates").select("chat_id").eq("chat_id", chat_id).execute().data
    if not rows:
        return None
    supabase.table("pending_bulk_updates").delete().eq("chat_id", chat_id).execute()
    return "Understood — nothing was changed."


UPDATE_TOOL = {
    "name": "record_updates",
    "description": (
        "Record what the property manager says has happened, by referring to "
        "the numbered tasks (T#) and unpaid draws (D#) in the provided list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "applicable": {
                "type": "boolean",
                "description": "False if the message isn't reporting task progress or draw payments at all.",
            },
            "task_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "A task reference from the list, e.g. T12."},
                        "status": {"type": "string", "enum": ["Pending", "In Progress", "Completed"]},
                        "percent": {
                            "type": ["number", "null"],
                            "description": "0-100. Only when the sender gave or clearly implied one (e.g. 'half done' = 50). Null otherwise — never invent a number.",
                        },
                        "evidence": {"type": "string", "description": "The words from the message this came from."},
                    },
                    "required": ["ref", "status", "evidence"],
                },
            },
            "draw_updates": {
                "type": "array",
                "description": "Draws the sender says were paid / received / released.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "A draw reference from the list, e.g. D2."},
                        "amount_paid": {
                            "type": ["number", "null"],
                            "description": "The amount actually paid, if stated and it differs from the scheduled amount (an overage or shortfall). Null otherwise.",
                        },
                        "evidence": {"type": "string"},
                    },
                    "required": ["ref", "evidence"],
                },
            },
            "schedule_shifts": {
                "type": "array",
                "description": "A whole unit's work being delayed/moved: its earliest start date is now the given date and everything after it moves by the same amount.",
                "items": {
                    "type": "object",
                    "properties": {
                        "unit_ref": {"type": "string", "description": "A unit reference from the list, e.g. U3."},
                        "new_start": {"type": "string", "description": "YYYY-MM-DD the unit's earliest work now starts."},
                        "evidence": {"type": "string"},
                    },
                    "required": ["unit_ref", "new_start", "evidence"],
                },
            },
            "unclear": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Things mentioned that could NOT be confidently matched to a listed task/draw, "
                    "or that aren't a status/payment (new ETAs, questions, delays). One short line each."
                ),
            },
        },
        "required": ["applicable", "task_updates", "draw_updates", "schedule_shifts", "unclear"],
    },
}

UPDATE_SYSTEM_PROMPT = """You are Jeeves, reading a reply from the property manager — usually \
to your daily summary — reporting what has been done on their renovation jobs. They write \
casually, in their own words, so a task is often described by a VARIANT of its name \
("kitchen paint" for "Kitchen paint or install new cabinets"; "the roof" for several roofing \
tasks). Use the numbered lists to match each thing they mention.

Rules:
- Report ONLY what the message says. "done/complete/finished/installed/paid off" -> Completed. \
"started / working on it / X% done / halfway" -> In Progress (percent only if stated or clearly \
implied). "not started / reset" -> Pending.
- A scope covering several listed tasks ("the whole roof") means every task that is clearly part of it.
- "draw (request) was paid / received / released" -> the matching draw in the DRAWS list. If it's \
unclear WHICH draw, put it in `unclear` rather than guessing.
- Never guess. If you can't tell which listed task a phrase means, put it in `unclear`.
- A property named WITHOUT a unit means ALL of its units: "Montclair demo done" completes the demo \
task in every unit at Montclair. Only when a unit IS named is the update limited to that unit.
- When no property is named at all and the same kind of work exists in several properties/units, \
DO NOT pick one — put it in `unclear` as "<what they said> — which property/unit?".
- A unit's work being delayed or pushed ("all unit 2 work pushed to start Oct 17") is a \
schedule_shift: unit_ref plus new_start (earliest start of that unit's work; the rest follows). \
Resolve dates against TODAY (given below); a date without a year is the next upcoming one.
- A draw paid for an amount that differs from the scheduled one still goes in draw_updates for the \
draw it belongs to, with amount_paid set to what was actually paid. Pick the draw from the \
context in the message (e.g. "against unit 3 labor" -> the Unit 3 draw); if it is truly not \
determinable, put it in `unclear`.
- Other new ETAs, questions and opinions are not updates: list them in `unclear`.
- If a message mentions both properties, handle each by its own tasks. If the property isn't \
stated, use whichever property the item clearly belongs to.

Call record_updates with the result."""


_GENERIC_NAME_WORDS = {
    "ave", "avenue", "st", "street", "rd", "road", "dr", "drive", "ln", "lane", "ct", "court",
    "cir", "circle", "blvd", "way", "pl", "place", "north", "south", "east", "west", "the",
}


def _name_tokens(property_name: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", property_name.lower())
        if w not in _GENERIC_NAME_WORDS and len(w) >= 3
    }


def _mentioned_property_ids(props: list[dict], text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return {p["id"] for p in props if _name_tokens(p["property_name"]) & words}


def _load_context(supabase: Client, text: str = "", keep_property_ids: set[str] | None = None):
    """(prompt_text, task_by_ref, draw_by_ref) covering every unarchived
    property: its tasks with current status, and its unpaid draws. Numbered
    refs (T1, D1…) let Claude answer by reference instead of retyping long
    names or ids."""
    props = [
        p for p in supabase.table("properties").select("id, property_name, archived").order("property_name").execute().data
        if not p.get("archived")
    ]
    # Send only the property (or properties) the message is about; the whole
    # portfolio only when it names none. Properties already in a pending
    # proposal stay in, so follow-ups keep their context.
    wanted = _mentioned_property_ids(props, text) | (keep_property_ids or set())
    if wanted:
        props = [p for p in props if p["id"] in wanted]
    task_by_ref: dict[str, dict] = {}
    draw_by_ref: dict[str, dict] = {}
    unit_by_ref: dict[str, dict] = {}
    lines: list[str] = [f"TODAY: {datetime.now(timezone.utc).date().isoformat()}"]
    t = d = u_n = 0
    for prop in props:
        units = supabase.table("units").select("id, unit_name").eq("property_id", prop["id"]).execute().data
        unit_name = {u["id"]: u["unit_name"] for u in units}
        items = []
        if units:
            items = (
                supabase.table("line_items")
                .select("id, unit_id, task_name, cost_group, status, percent_complete, start_date, estimated_end_date")
                .in_("unit_id", list(unit_name)).execute().data
            )
        lines.append(f"\nPROPERTY: {prop['property_name']}")
        uref_by_id = {}
        for uid, uname in unit_name.items():
            u_n += 1
            uref_by_id[uid] = f"U{u_n}"
            starts = [i["start_date"] for i in items if i["unit_id"] == uid and i.get("start_date")]
            unit_by_ref[f"U{u_n}"] = {
                "unit_id": uid, "unit_name": uname, "property_id": prop["id"],
                "property_name": prop["property_name"], "earliest": min(starts) if starts else None,
            }
            lines.append(f"  UNIT U{u_n} = {uname} (earliest work starts {min(starts) if starts else 'unscheduled'})")
        for item in items:
            t += 1
            ref = f"T{t}"
            pct = float(item.get("percent_complete") or 0)
            status = item.get("status") or "Pending"
            task_by_ref[ref] = {
                "line_item_id": item["id"], "property_id": prop["id"],
                "property_name": prop["property_name"], "unit_name": unit_name.get(item["unit_id"], "?"),
                "task_name": item["task_name"], "status": status, "percent": pct,
            }
            shown = f"{status} {pct:.0f}%" if status == "In Progress" else status
            lines.append(f"  {ref} | {unit_name.get(item['unit_id'], '?')} | {item.get('cost_group') or '-'} | {item['task_name']} | now: {shown}")
        draws = (
            supabase.table("draw_milestones").select("id, milestone_name, draw_amount, status")
            .eq("property_id", prop["id"]).neq("status", "Released").execute().data
        )
        if draws:
            lines.append("  DRAWS NOT YET MARKED PAID:")
        for draw in draws:
            d += 1
            ref = f"D{d}"
            draw_by_ref[ref] = {
                "milestone_id": draw["id"], "property_id": prop["id"],
                "property_name": prop["property_name"], "name": draw["milestone_name"],
                "amount": float(draw["draw_amount"] or 0),
            }
            lines.append(f"  {ref} | {draw['milestone_name']} | ${float(draw['draw_amount'] or 0):,.0f}")
    return "\n".join(lines), task_by_ref, draw_by_ref, unit_by_ref


def _read_message(text: str, context: str, conversation: str = "") -> dict:
    client = _anthropic_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=3000,
        system=UPDATE_SYSTEM_PROMPT,
        tools=[UPDATE_TOOL],
        tool_choice={"type": "tool", "name": "record_updates"},
        messages=[{"role": "user", "content": (
            f"{context}\n\n---\n{conversation}Message from the property manager:\n{text}"
        )}],
    )
    return next(block for block in message.content if block.type == "tool_use").input


def _target(current: dict, status: str, percent) -> tuple[str, float]:
    """The (status, percent) to store — same rules as the app: Completed is
    100, Pending is 0, In Progress keeps a stated percent, else the existing
    one if it's genuinely partial."""
    if status == "Completed":
        return "Completed", 100.0
    if status == "Pending":
        return "Pending", 0.0
    if percent is not None:
        return "In Progress", max(0.0, min(100.0, float(percent)))
    existing = float(current.get("percent") or 0)
    return "In Progress", existing if 0 < existing < 100 else 0.0


def _preview(changes: dict, not_acted: list[str], merged: bool) -> str:
    esc = html.escape
    by_prop: dict[str, dict[str, list[str]]] = {}
    for c in changes["tasks"]:
        sec = by_prop.setdefault(c["property_name"], {})
        bucket = {"Completed": "✅ Complete", "In Progress": "🔧 In progress", "Pending": "⏪ Back to not started"}[c["status"]]
        suffix = f" — {c['percent']:.0f}%" if c["status"] == "In Progress" and c["percent"] else ""
        sec.setdefault(bucket, []).append(f"{esc(c['unit_name'])}: {esc(c['task_name'])}{suffix}")
    for dr in changes["draws"]:
        line = f"{esc(dr['name'])} — ${dr['amount']:,.0f}"
        sched = dr.get("scheduled", dr["amount"])
        if abs(dr["amount"] - sched) >= 0.5:
            diff = dr["amount"] - sched
            line += f" paid (scheduled ${sched:,.0f}; ${abs(diff):,.0f} {'over' if diff > 0 else 'under'} — I shall record the amount actually paid)"
        by_prop.setdefault(dr["property_name"], {}).setdefault("💰 Draws marked as paid", []).append(line)
    for sh in changes.get("shifts", []):
        d = sh["days"]
        by_prop.setdefault(sh["property_name"], {}).setdefault("📅 Schedule moved", []).append(
            f"{esc(sh['unit_name'])}: earliest start {sh['earliest']} → {sh['new_start']} "
            f"({abs(d)} day{'s' if abs(d) != 1 else ''} {'later' if d > 0 else 'earlier'}); all its tasks move together")

    parts = ["🎩 " + ("Added to your pending changes, sir. " if merged else "")
             + "Here is what I shall record:"]
    for prop, sections in by_prop.items():
        parts.append(f"\n<b>{esc(prop)}</b>")
        for title, entries in sections.items():
            parts.append(f"{title} ({len(entries)}):")
            shown = entries[:30]
            parts += [f"  • {e}" for e in shown]
            if len(entries) > len(shown):
                parts.append(f"  … and {len(entries) - len(shown)} more")
    if not_acted:
        parts.append("\n❓ I did not act on:")
        parts += [f"  • {esc(u)}" for u in not_acted[:8]]
    parts.append("\nReply <b>yes</b> to apply, <b>no</b> to cancel — or send corrections or further items.")
    return "\n".join(parts)[:3900]


def _load_pending(supabase: Client, chat_id: str) -> dict | None:
    rows = supabase.table("pending_bulk_updates").select("*").eq("chat_id", chat_id).execute().data
    if not rows or not rows[0].get("changes"):
        return None
    created = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - created > PROPOSAL_TTL:
        return None
    return rows[0]["changes"]


def _conversation_so_far(existing: dict | None) -> str:
    """What was said earlier in this same pending conversation, so a short
    follow-up ("the flooring I meant is in unit B") can be understood
    against the question it answers."""
    if not existing:
        return ""
    earlier = existing.get("history") or []
    asked = existing.get("unclear") or []
    out = "This message continues a conversation still awaiting confirmation.\n"
    if earlier:
        out += "Earlier messages from the manager:\n" + "\n".join(f"- {m}" for m in earlier[-4:]) + "\n"
    if asked:
        out += "Items I asked them to clarify:\n" + "\n".join(f"- {u}" for u in asked[:8]) + "\n"
    return out + "\n"


def propose_bulk_update(supabase: Client, chat_id: str, text: str) -> str:
    """Reads a message from the owner, works out every task/draw it refers
    to, stores the proposal and returns a preview asking for confirmation.
    ALWAYS returns an answer — a DM to Jeeves is never silently ignored."""
    existing = _load_pending(supabase, chat_id)
    try:
        keep = {c["property_id"] for k in ("tasks", "draws", "shifts") for c in (existing or {}).get(k, [])}
        context, task_by_ref, draw_by_ref, unit_by_ref = _load_context(
            supabase, " ".join([text] + list((existing or {}).get("history") or [])[-2:]), keep)
        result = _read_message(text, context, _conversation_so_far(existing))
    except Exception as e:
        print(f"WARNING: couldn't read update message: {e}")
        return "I had trouble reading that just now, sir — could you try again in a moment?"

    task_changes, seen = [], set()
    already_current = 0
    for u in result.get("task_updates", []):
        task = task_by_ref.get(u.get("ref"))
        if not task or task["line_item_id"] in seen:
            continue
        seen.add(task["line_item_id"])
        status, percent = _target(task, u["status"], u.get("percent"))
        if status == task["status"] and abs(percent - task["percent"]) < 0.5:
            already_current += 1
            continue
        task_changes.append({**task, "new_status": status, "status": status,
                             "percent": percent, "old_status": task["status"]})
    draw_changes = []
    for u in result.get("draw_updates", []):
        if u.get("ref") not in draw_by_ref:
            continue
        draw = dict(draw_by_ref[u["ref"]])
        paid = u.get("amount_paid")
        draw["scheduled"] = draw["amount"]
        if paid is not None and abs(float(paid) - draw["amount"]) >= 0.5:
            draw["amount"] = float(paid)
        draw_changes.append(draw)
    shift_changes = []
    for u in result.get("schedule_shifts", []):
        unit = unit_by_ref.get(u.get("unit_ref"))
        try:
            new_start = datetime.fromisoformat(u["new_start"]).date()
            old_start = datetime.fromisoformat(unit["earliest"]).date() if unit and unit["earliest"] else None
        except (ValueError, KeyError, TypeError):
            continue
        if not unit or not old_start or new_start == old_start:
            continue
        days = (new_start - old_start).days
        rows = supabase.table("line_items").select("id, start_date, estimated_end_date").eq("unit_id", unit["unit_id"]).execute().data
        plan = {}
        for r in rows:
            plan[r["id"]] = {
                col: [r[col], (datetime.fromisoformat(r[col]).date() + timedelta(days=days)).isoformat()]
                for col in ("start_date", "estimated_end_date") if r.get(col)
            }
        shift_changes.append({**unit, "new_start": new_start.isoformat(), "days": days, "plan": plan})
    not_acted = list(result.get("unclear", []))

    if not task_changes and not draw_changes and not shift_changes and existing is not None:
        existing["history"] = (existing.get("history") or []) + [text]
        existing["unclear"] = not_acted
        supabase.table("pending_bulk_updates").update({"changes": existing}).eq("chat_id", chat_id).execute()
        note = "Nothing new to add from that, sir."
        if not_acted:
            note += " I still couldn't place:\n" + "\n".join(f"  • {html.escape(x)}" for x in not_acted[:8]) + \
                "\n\nCould you say which?"
        return note + "\n\n" + _preview(existing, [], False)

    if not task_changes and not draw_changes and not shift_changes:
        base = "I didn't find anything to change, sir"
        if already_current:
            base += f" — the {already_current} item(s) you mentioned are already recorded that way"
        if not_acted:
            return base + ". I couldn't place:\n" + "\n".join(f"  • {x}" for x in not_acted[:8]) + \
                "\n\nCould you name the task(s) more closely?"
        return base + ". To update the schedule, tell me what's done or how far along — for " \
            "example “the roof is complete at 809”, or reply to the daily summary line by line."

    # Telegram splits long messages: fold this one into any still-pending proposal.
    merged = existing is not None
    changes = existing or {"tasks": [], "draws": [], "shifts": []}
    by_task = {c["line_item_id"]: c for c in changes["tasks"]}
    for c in task_changes:
        by_task[c["line_item_id"]] = c
    by_draw = {d["milestone_id"]: d for d in changes["draws"]}
    for d in draw_changes:
        by_draw[d["milestone_id"]] = d
    by_shift = {sh["unit_id"]: sh for sh in (existing or {}).get("shifts", [])}
    for sh in shift_changes:
        by_shift[sh["unit_id"]] = sh
    changes = {
        "tasks": list(by_task.values()), "draws": list(by_draw.values()), "shifts": list(by_shift.values()),
        "history": ((existing or {}).get("history") or []) + [text],
        "unclear": not_acted,
    }

    supabase.table("pending_bulk_updates").upsert({
        "chat_id": chat_id, "changes": changes, "summary": f"{len(changes['tasks'])} task(s), {len(changes['draws'])} draw(s), {len(changes['shifts'])} shift(s)",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    reply = _preview(changes, not_acted, merged)
    if already_current:
        reply = reply.replace("\n\nReply", f"\n({already_current} you mentioned were already recorded that way.)\n\nReply", 1)
    return reply


def apply_pending_update(supabase: Client, chat_id: str) -> str | None:
    """Applies the pending proposal, if any. None if there's nothing pending
    (the caller falls through to treating the message as new). A proposal
    older than PROPOSAL_TTL is discarded, never applied."""
    rows = supabase.table("pending_bulk_updates").select("*").eq("chat_id", chat_id).execute().data
    if not rows:
        return None
    pending = rows[0]
    created = datetime.fromisoformat(pending["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - created > PROPOSAL_TTL:
        supabase.table("pending_bulk_updates").delete().eq("chat_id", chat_id).execute()
        return (
            "That proposal is more than 12 hours old, sir, so I've discarded it rather "
            "than risk applying it to a schedule that may have changed. Please send "
            "the update again."
        )
    changes = pending.get("changes")
    if not changes:  # an old-format proposal from before this upgrade
        supabase.table("pending_bulk_updates").delete().eq("chat_id", chat_id).execute()
        return "That proposal was from before an upgrade, sir — please send the update again."

    by_property: dict[str, dict] = {}
    for c in changes["tasks"]:
        supabase.table("line_items").update(
            {"status": c["status"], "percent_complete": c["percent"]}
        ).eq("id", c["line_item_id"]).execute()
        entry = by_property.setdefault(c["property_id"], {"name": c["property_name"], "lines": [], "draws": 0})
        suffix = f" ({c['percent']:.0f}%)" if c["status"] == "In Progress" and c["percent"] else ""
        entry["lines"].append(f"{c['unit_name']}: {c['task_name']} — now {c['status']}{suffix}")

    shifted = 0
    for sh in changes.get("shifts", []):
        # Each task has an absolute [old, new] date pair fixed when proposed, so
        # re-running after a crash sets the same values instead of shifting twice.
        delta = timedelta(days=sh["days"])
        for item_id, cols in (sh.get("plan") or {}).items():
            current = supabase.table("line_items").select("start_date, estimated_end_date").eq("id", item_id).execute().data
            if not current:
                continue
            upd = {}
            for col, (old, new) in cols.items():
                now_val = current[0].get(col)
                if now_val == new:
                    continue  # already moved
                upd[col] = new if now_val == old else (
                    (datetime.fromisoformat(now_val).date() + delta).isoformat() if now_val else new)
            if upd:
                supabase.table("line_items").update(upd).eq("id", item_id).execute()
        entry = by_property.setdefault(sh["property_id"], {"name": sh["property_name"], "lines": [], "draws": 0})
        entry["lines"].append(f"{sh['unit_name']}: whole schedule moved {abs(sh['days'])} day(s) "
                              f"{'later' if sh['days'] > 0 else 'earlier'} — work now begins {sh['new_start']}")
        shifted += 1

    draws_paid = 0
    for d in changes["draws"]:
        current = supabase.table("draw_milestones").select("status").eq("id", d["milestone_id"]).execute().data
        if current and current[0]["status"] != "Released":
            sched = d.get("scheduled", d["amount"])
            if abs(d["amount"] - sched) >= 0.5:
                # Record what was really paid so the budget shows the overage.
                supabase.table("draw_milestones").update({"draw_amount": d["amount"]}).eq("id", d["milestone_id"]).execute()
            release_draw_milestone(supabase, d["milestone_id"])
            over = f" (scheduled ${sched:,.2f}, ${d['amount'] - sched:+,.2f})" if abs(d["amount"] - sched) >= 0.5 else ""
            log_activity(supabase, d["property_id"], "draw",
                         f"{d['name']} marked paid (via Jeeves) — ${d['amount']:,.2f}{over}")
            by_property.setdefault(d["property_id"], {"name": d["property_name"], "lines": [], "draws": 0})["draws"] += 1
            draws_paid += 1

    supabase.table("pending_bulk_updates").delete().eq("chat_id", chat_id).execute()

    for property_id, entry in by_property.items():
        if not entry["lines"]:
            continue
        log_activity(supabase, property_id, "schedule", f"{len(entry['lines'])} task update(s) via Jeeves")
        row = supabase.table("properties").select("telegram_chat_id").eq("id", property_id).execute().data
        send_and_pin_batch_schedule_update(
            property_name=entry["name"], property_id=property_id, changes=entry["lines"],
            chat_id=row[0].get("telegram_chat_id") if row else None,
        )

    bits = []
    if changes["tasks"]:
        bits.append(f"updated {len(changes['tasks'])} task(s)")
    if shifted:
        bits.append(f"moved {shifted} unit schedule(s)")
    if draws_paid:
        bits.append(f"marked {draws_paid} draw(s) as paid")
    return "🎩 Done, sir — " + " and ".join(bits) + "."
