import base64
import re

from utils.anthropic_client import get_anthropic_client, get_model


class NotAReceiptError(ValueError):
    """Raised when the input didn't yield a readable receipt (no numeric
    total). Distinct from a transient API/network failure so callers can
    tell "this email isn't a receipt, stop retrying it" apart from "try
    again next run"."""


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
            "job_or_property_reference": {
                "type": ["string", "null"],
                "description": (
                    "Any job name, PO number, or address printed on the "
                    "receipt that could identify which property the "
                    "purchase was for. Null if none is printed."
                ),
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
- job_or_property_reference: any job name, PO number, or address printed on it

Call the record_receipt tool with the result. Do not include any commentary \
outside of the tool call."""


def _validated_receipt(tool_input: dict) -> dict:
    """Claude occasionally fills a required numeric field with a
    placeholder string like "<UNKNOWN>" instead of a number when the
    input has no real receipt data (seen in production: a Home Depot
    email whose actual receipt was in a PDF attachment, leaving only
    boilerplate in the body). That crashed the Postgres insert and, since
    the email stayed unprocessed, the nightly job every night after.
    Refusing a non-numeric total here turns that into a clean "not a
    readable receipt" instead of junk data."""
    total = tool_input.get("total_cost")
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        raise NotAReceiptError(f"no numeric total found (got {total!r})")
    return tool_input


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
    return _validated_receipt(tool_use.input)


PDF_SYSTEM_PROMPT = """You are extracting structured data from a hardware/home-\
improvement store receipt delivered as a PDF (Home Depot, Lowe's, or similar).

Extract:
- store_name
- purchase_date (YYYY-MM-DD)
- total_cost (the final total actually charged, not a subtotal)
- line_items: every individual item purchased with its cost
- job_or_property_reference: any job name, PO number, or address printed on it

If you cannot find a real numeric total on the document, do NOT invent one or \
write a placeholder — this may not be a receipt at all.

Call the record_receipt tool with the result. Do not include any commentary \
outside of the tool call."""


def parse_receipt_pdf(pdf_bytes: bytes) -> dict:
    """Same as parse_receipt_text, for a receipt delivered as a PDF
    attachment (Home Depot emails put the whole receipt in one — the
    email body is only a thank-you note). Claude reads the PDF natively."""
    client = get_anthropic_client()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    message = client.messages.create(
        model=get_model(),
        max_tokens=2048,
        system=PDF_SYSTEM_PROMPT,
        tools=[RECEIPT_TOOL],
        tool_choice={"type": "tool", "name": "record_receipt"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": "Extract this receipt."},
                ],
            }
        ],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return _validated_receipt(tool_use.input)


MATCH_UNIT_TOOL = {
    "name": "record_unit_match",
    "description": (
        "Record which unit/area of a property a purchase was for, based on "
        "the job name written on the receipt."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "matched_index": {
                "type": ["integer", "null"],
                "description": (
                    "Index (from the provided list) of the unit or area the "
                    "job name refers to, or null if it doesn't name one "
                    "(e.g. it's only the street address) — don't guess."
                ),
            }
        },
        "required": ["matched_index"],
    },
}


def match_unit_from_reference(reference_text: str, units: list[dict]) -> str | None:
    """units: [{"id": ..., "unit_name": ...}, ...]. Reads the job name
    written on a receipt (e.g. "809 Fred Unit 3 kitchen") and returns the
    id of the unit/area it names, or None if it names none. Returns None
    rather than guessing — an unassigned purchase is easy for the PM to
    file; one filed under the wrong unit quietly skews that unit's
    materials spend."""
    if not (reference_text or "").strip() or not units:
        return None
    listing = "\n".join(f"{i}. {u['unit_name']}" for i, u in enumerate(units))

    client = get_anthropic_client()
    message = client.messages.create(
        model=get_model(),
        max_tokens=256,
        system=(
            "A contractor writes a job name on a hardware-store receipt, "
            'e.g. "809 Fred Unit 3 kitchen". Given the text and the '
            "property's list of units/areas, return the index of the "
            "unit or area it names. If it names none — only the street "
            "address, or something that doesn't correspond to a listed "
            "unit — return null. Never guess."
        ),
        tools=[MATCH_UNIT_TOOL],
        tool_choice={"type": "tool", "name": "record_unit_match"},
        messages=[
            {
                "role": "user",
                "content": f"Receipt job name: {reference_text}\n\nUnits:\n{listing}",
            }
        ],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    index = tool_use.input.get("matched_index")
    if isinstance(index, int) and 0 <= index < len(units):
        return units[index]["id"]
    return None


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
