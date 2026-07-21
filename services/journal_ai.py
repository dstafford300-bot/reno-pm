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
                    "that are genuine progress notes, field updates, "
                    "questions/decisions about the work, or photos — worth "
                    "logging. Exclude greetings, small talk, logistics "
                    "chatter unrelated to the work itself, or messages with "
                    "no informational value. When in doubt about a photo, "
                    "keep it — a picture from the job site is inherently "
                    "useful visual record even without caption text."
                ),
            },
        },
        "required": ["relevant_indices"],
    },
}

SYSTEM_PROMPT = """You are Jeeves, an assistant reviewing a day's messages from a \
construction job-site Telegram group to decide what belongs in the official \
Project Journal.

Keep: genuine field/progress notes, decisions, problems encountered, questions \
about the work, and any photo (a job-site photo is valuable on its own even \
without much caption text).

Discard: greetings, small talk, scheduling logistics unrelated to the work \
itself ("running 10 min late"), acknowledgements ("ok", "thanks", "sounds \
good"), or anything with no informational value about the project.

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
