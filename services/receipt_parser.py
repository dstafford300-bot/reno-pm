import re

from utils.anthropic_client import get_anthropic_client, get_model

RECEIPT_TOOL = {
    "name": "record_receipt",
    "description": (
        "Record structured data extracted from a raw hardware/home-"
        "improvement store receipt (e.g. Home Depot Pro Xtra, Lowe's Pro)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "store_name": {"type": "string"},
            "purchase_date": {
                "type": "string",
                "description": "YYYY-MM-DD, best guess if the year is ambiguous",
            },
            "total_cost": {"type": "number"},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "cost": {"type": "number"},
                    },
                    "required": ["description", "cost"],
                },
            },
        },
        "required": ["store_name", "total_cost"],
    },
}

SYSTEM_PROMPT = """You are extracting structured data from raw, messily-formatted \
copy-pasted text of a hardware/home-improvement store e-receipt (Home Depot Pro \
Xtra, Lowe's Pro, or similar). The text may have odd line breaks, repeated \
whitespace, or extraneous header/footer content (loyalty program text, barcodes \
rendered as text, etc.) — ignore anything that isn't part of the actual purchase.

Extract:
- store_name
- purchase_date (YYYY-MM-DD)
- total_cost (the final total actually charged, not a subtotal)
- line_items: every individual item purchased with its cost

Call the record_receipt tool with the result. Do not include any commentary \
outside of the tool call."""


def parse_receipt_text(raw_text: str) -> dict:
    client = get_anthropic_client()
    message = client.messages.create(
        model=get_model(),
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[RECEIPT_TOOL],
        tool_choice={"type": "tool", "name": "record_receipt"},
        messages=[{"role": "user", "content": raw_text}],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input


def match_property_from_text(text: str, properties: list[dict]) -> str | None:
    """properties: [{"id": ..., "property_name": ...}, ...].

    Returns the id of the property whose full name, or a word within it
    that's unique to just that one property (e.g. "809" out of "809 Fred
    Shuttlesworth Circle"), appears in `text` as a whole word/number.

    Words shared across multiple properties (e.g. "Fred", "Shuttlesworth",
    "Circle" when two properties are on the same street) are deliberately
    NOT used to disambiguate — matching on a shared word would silently
    pick whichever property happens to be checked first, which is wrong
    more often than not when a distinguishing number like "811" is right
    there in the text. Only a word unique to exactly one property counts.
    """
    lowered = text.lower()

    for prop in properties:
        if prop["property_name"].lower() in lowered:
            return prop["id"]

    token_owners: dict[str, set[str]] = {}
    for prop in properties:
        for token in prop["property_name"].split():
            token_clean = token.strip(",.-—").lower()
            if len(token_clean) < 3:
                continue
            token_owners.setdefault(token_clean, set()).add(prop["id"])

    for prop in properties:
        for token in prop["property_name"].split():
            token_clean = token.strip(",.-—").lower()
            if len(token_clean) < 3:
                continue
            if len(token_owners.get(token_clean, set())) != 1:
                continue  # shared across multiple properties — ambiguous
            if re.search(rf"\b{re.escape(token_clean)}\b", lowered):
                return prop["id"]
    return None


def extract_amount_hint(text: str) -> float | None:
    """Pulls a dollar amount out of a short free-text hint like '$120 for
    lumber'. Returns None if no amount pattern is found."""
    match = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text or "")
    if not match:
        return None
    return float(match.group(1).replace(",", ""))
