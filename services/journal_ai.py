from utils.anthropic_client import get_anthropic_client, get_model

RELEVANCE_TOOL = {
    "name": "classify_journal_relevance",
    "description": (
        "Decide which messages from a job-site group chat are meaningful "
        "field/progress updates worth logging in the project journal."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "0-based indices (from the provided list) of messages "
                    "worth keeping: text that states a project fact "
                    "(progress, a problem, a decision, a spec/cost/date), "
                    "and every photo. Exclude questions, requests, "
                    "greetings, acknowledgements and conversational "
                    "back-and-forth — when in doubt about text, leave it "
                    "out."
                ),
            },
        },
        "required": ["relevant_indices"],
    },
}

SYSTEM_PROMPT = """You are Jeeves, deciding what belongs in the official Project \
Journal from a construction job-site Telegram group. The journal is a record of \
PERTINENT project information only — not a chat log. Be strict: when in doubt \
about a text message, leave it out.

Keep ONLY text that states project facts: work completed or started, a problem \
or defect found, a decision made, a measurement/spec/material/cost detail, a \
delivery or inspection date, or access/site information someone will need later. \
A message must be useful to someone reading the journal weeks from now.

Discard: questions and requests ("can you send the schedule?", "is a lock box \
there?"), greetings, thanks, acknowledgements ("ok", "will do", "sounds good"), \
back-and-forth negotiation, scheduling chatter, opinions, and anything that only \
makes sense as part of a live conversation. A question is NOT a project fact, \
even if it's about the work. Messages from the property manager asking or \
instructing are usually conversation, not records — keep one only if it states a \
fact or decision (e.g. "Stove for unit 3 is being delivered Oct 9th").

Photos: keep every photo — a job-site picture is a valuable record by itself.

Call the classify_journal_relevance tool with the indices of messages worth \
keeping. Do not include any commentary outside of the tool call."""


def filter_relevant_messages(messages: list[dict]) -> list[dict]:
    """Given a list of journal-entry-shaped dicts (message_text,
    photo_file_id, etc.), return the subset Jeeves judges worth keeping in
    the journal. Empty input returns empty output without an API call."""
    if not messages:
        return []

    lines = []
    for i, m in enumerate(messages):
        text = m.get("message_text") or "(no text)"
        has_photo = " [has photo]" if m.get("photo_file_id") else ""
        lines.append(f"{i}. {m.get('author_name', 'Unknown')}: {text}{has_photo}")

    client = get_anthropic_client()
    message = client.messages.create(
        model=get_model(),
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[RELEVANCE_TOOL],
        tool_choice={"type": "tool", "name": "classify_journal_relevance"},
        messages=[{"role": "user", "content": "\n".join(lines)}],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    relevant_indices = set(tool_use.input.get("relevant_indices", []))
    return [m for i, m in enumerate(messages) if i in relevant_indices]
