"""Matty: a dedicated Telegram bot for the legacy building materials log —
separate from Jeeves so no trigger phrase is needed. Every message Matty
receives is material-related by definition (it's a single-purpose bot), so
each one is classified as either a LOG request (a new material, via photo
and/or text) or a LOOKUP question (about something already logged), then
handled accordingly against the "Legacy Materials Log" Airtable base.

Deliberately not using Streamlit (utils.anthropic_client/utils.settings)
— this module runs inside the standalone webhook process (webhook_main.py),
not the Streamlit app, so it reads its own config directly from the
environment.
"""

import base64
import html
import os

from anthropic import Anthropic

from services.airtable_client import create_material_record, search_material_records

CATEGORY_CHOICES = [
    "Paint",
    "Tile",
    "Flooring",
    "Plumbing",
    "Electrical",
    "Hardware",
    "Other",
]
PROJECT_TYPE_CHOICES = ["Legacy Project", "Active PM Software Project"]
DEFAULT_PROJECT_TYPE = "Legacy Project"


def _anthropic_client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5"


CLASSIFY_INTENT_TOOL = {
    "name": "classify_intent",
    "description": (
        "Decide whether a message to Matty (a materials-log assistant) is "
        "a request to log a new material, or a question looking up "
        "something already logged."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["log", "lookup"],
                "description": (
                    "'log' for a new material to record (a photo of a "
                    "label and/or text describing something purchased/"
                    "installed). 'lookup' for a question about materials "
                    "already logged."
                ),
            }
        },
        "required": ["intent"],
    },
}

CLASSIFY_SYSTEM_PROMPT = """You are Matty, an assistant that exclusively handles a \
legacy building-materials log — nothing else. Every message you receive is one of \
exactly two things:

1. LOG — a request to record a new material: a photo of a product label, and/or \
text describing a property address, what the item is, brand, color, SKU, etc. \
A message with a photo attached is almost always a log request.

2. LOOKUP — a question about something already logged, e.g. "what paint did we \
use at 123 Main St", "do we have the SKU for the tile at Lincoln Heights", "what \
flooring is in the upstairs bath at Probasco". Phrased as a question — what/which/\
do we have/did we use, or ending in "?" — even without a photo.

Call classify_intent with your decision."""


def classify_intent(text: str, has_photo: bool) -> str:
    client = _anthropic_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=64,
        system=CLASSIFY_SYSTEM_PROMPT,
        tools=[CLASSIFY_INTENT_TOOL],
        tool_choice={"type": "tool", "name": "classify_intent"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Has photo attached: {has_photo}\n"
                    f"Message text: {text or '(none)'}"
                ),
            }
        ],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input.get("intent", "log")


EXTRACT_MATERIAL_TOOL = {
    "name": "record_material",
    "description": (
        "Record structured details about a building material, combining "
        "what's visible on the product label photo with what the sender "
        "wrote in their message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "property_address": {
                "type": "string",
                "description": "The property address mentioned in the message text.",
            },
            "location_in_house": {
                "type": "string",
                "description": (
                    "Room/area mentioned in the message text, e.g. "
                    "'Upstairs Bath', 'Kitchen'."
                ),
            },
            "category": {
                "type": "string",
                "enum": CATEGORY_CHOICES,
                "description": (
                    "Best-fit category. Infer from the label/item if the "
                    "message doesn't state one explicitly."
                ),
            },
            "brand_manufacturer": {
                "type": ["string", "null"],
                "description": "Brand/manufacturer name visible on the label.",
            },
            "item_name_description": {
                "type": ["string", "null"],
                "description": (
                    "What the item is, e.g. 'Interior Eggshell Paint', "
                    "'Porcelain Floor Tile'."
                ),
            },
            "color_finish_size": {
                "type": ["string", "null"],
                "description": (
                    "Color/finish/size details visible on the label, e.g. "
                    "'Agreeable Gray, Eggshell, 1 Gallon'. If this is a "
                    "CUSTOM/STORE-MATCHED color (a mixing ticket rather than "
                    "a standard catalog color name), a name like 'Custom "
                    "Color Match' alone is USELESS for reproducing it — put "
                    "the base paint name/number here, and put the complete "
                    "colorant tint formula (every colorant code with its "
                    "exact amount, transcribed as precisely as you can read "
                    "it) in `notes`."
                ),
            },
            "model_sku_barcode": {
                "type": ["string", "null"],
                "description": "Model number, SKU, or barcode number on the label.",
            },
            "project_type": {
                "type": ["string", "null"],
                "enum": PROJECT_TYPE_CHOICES + [None],
                "description": (
                    "Only set this if the sender's message explicitly "
                    "states a project type — otherwise leave null and it "
                    "will default to 'Legacy Project'."
                ),
            },
            "notes": {
                "type": ["string", "null"],
                "description": (
                    "Any extra detail from the sender's message that didn't "
                    "map into the other fields. For a custom color match, "
                    "this MUST include the full tint formula table — every "
                    "colorant line, transcribed exactly as printed (code + "
                    "amount). If any part of that formula is too small/"
                    "blurry to read with confidence, say so explicitly "
                    "(e.g. 'formula partly illegible in photo — colorant "
                    "amounts unclear') rather than silently omitting or "
                    "guessing at digits — a wrong tint number is worse than "
                    "an honest gap, since it would reproduce the wrong "
                    "color with false confidence."
                ),
            },
        },
        "required": ["property_address", "location_in_house", "category"],
    },
}

EXTRACT_SYSTEM_PROMPT = """You are Matty, extracting a legacy building material \
record from a photo of a product label (paint can, tile box, plumbing part, etc.) \
and a short accompanying message from the property manager.

Read the label photo carefully for brand, item description, color/finish/size, \
and any model/SKU/barcode number. Read the message text for the property address, \
room/location, and category — infer the category from the item itself if the \
message doesn't state one.

If the label is a paint store mixing ticket (a custom/computer-matched color, \
not a standard catalog name), the tint formula printed on it is the ONLY way \
to reproduce that exact color again — read it as carefully as you can and \
transcribe the base name plus every colorant code and amount into the notes \
field, per its description. Flag anything illegible rather than guessing at a \
digit.

Call the record_material tool with the result. Do not include any commentary \
outside of the tool call."""


TEXT_ONLY_SYSTEM_PROMPT = """You are Matty, extracting a legacy building material \
record from a text message alone (no photo was attached) — the sender typed the \
label/product details themselves rather than photographing them.

Read the message for the property address, room/location, category, brand, item \
description, color/finish/size, and any model/SKU/barcode number.

Call the record_material tool with the result. Do not include any commentary \
outside of the tool call."""


def extract_material_fields(image_bytes: bytes, caption_text: str) -> dict:
    client = _anthropic_client()
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    message = client.messages.create(
        model=_model(),
        max_tokens=1024,
        system=EXTRACT_SYSTEM_PROMPT,
        tools=[EXTRACT_MATERIAL_TOOL],
        tool_choice={"type": "tool", "name": "record_material"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Sender's message: {caption_text}",
                    },
                ],
            }
        ],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input


def extract_material_fields_from_text(caption_text: str) -> dict:
    client = _anthropic_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=1024,
        system=TEXT_ONLY_SYSTEM_PROMPT,
        tools=[EXTRACT_MATERIAL_TOOL],
        tool_choice={"type": "tool", "name": "record_material"},
        messages=[{"role": "user", "content": caption_text}],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input


def _is_sufficient(parsed: dict) -> bool:
    """A record is only worth writing if we know WHERE (an address) and
    WHAT (some description of the item) — otherwise every required field
    Claude was forced to fill gets a meaningless placeholder (seen in
    practice: "<UNKNOWN>" for location_in_house when a question got
    mis-routed here). Better to ask the sender to clarify than write a
    near-empty record to Airtable."""
    address = (parsed.get("property_address") or "").strip()
    what = (
        parsed.get("item_name_description")
        or parsed.get("brand_manufacturer")
        or parsed.get("model_sku_barcode")
    )
    return bool(address) and bool(what)


def log_material_from_message(
    caption_text: str,
    image_bytes: bytes | None = None,
    photo_public_url: str | None = None,
) -> dict:
    """Extracts fields (via Vision if a photo was sent, text-only
    otherwise), writes the Airtable record, and returns
    {"success": bool, "confirmation_text": str}. Never raises — callers
    are Telegram webhook handlers that should always be able to reply
    with *something*, even on failure."""
    try:
        if image_bytes is not None:
            parsed = extract_material_fields(image_bytes, caption_text)
        else:
            parsed = extract_material_fields_from_text(caption_text)
    except Exception as e:
        return {
            "success": False,
            "confirmation_text": (
                f"I couldn't make sense of that, sorry — {html.escape(str(e))}"
            ),
        }

    if not _is_sufficient(parsed):
        return {
            "success": False,
            "confirmation_text": (
                "I'm not confident I caught enough detail to log this "
                "properly — could you confirm the property address and "
                "what the item is?"
            ),
        }

    fields = {
        "Property Address": parsed.get("property_address") or "",
        "Location in House": parsed.get("location_in_house") or "",
        "Category": parsed.get("category") or "Other",
        "Brand / Manufacturer": parsed.get("brand_manufacturer"),
        "Item Name & Description": parsed.get("item_name_description"),
        "Color / Finish / Size": parsed.get("color_finish_size"),
        "Model / SKU / Barcode": parsed.get("model_sku_barcode"),
        "Project Type": parsed.get("project_type") or DEFAULT_PROJECT_TYPE,
        "Notes": parsed.get("notes"),
    }
    if photo_public_url:
        fields["Photo"] = [{"url": photo_public_url}]
    fields = {k: v for k, v in fields.items() if v not in (None, "")}

    record = create_material_record(fields)
    if not record:
        return {
            "success": False,
            "confirmation_text": (
                "I read that but couldn't reach the materials log — "
                "please check the Airtable connection."
            ),
        }

    brand = parsed.get("brand_manufacturer")
    color = parsed.get("color_finish_size")
    what = " ".join(part for part in [brand, color] if part) or parsed.get(
        "item_name_description", "item"
    )
    location_label = f"{parsed.get('property_address')} {parsed.get('location_in_house')}".strip()
    return {
        "success": True,
        "confirmation_text": (
            f"✅ Logged: {html.escape(what)} under {html.escape(location_label)}."
        ),
    }


LOOKUP_QUERY_TOOL = {
    "name": "record_lookup_query",
    "description": (
        "Extract search parameters from a plain-English question about "
        "previously logged building materials."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "address_query": {
                "type": "string",
                "description": (
                    "The property name/address (or a distinctive word from "
                    "it) the question is asking about."
                ),
            },
            "category": {
                "type": ["string", "null"],
                "enum": CATEGORY_CHOICES + [None],
                "description": "Only set if the question clearly refers to one category.",
            },
            "location": {
                "type": ["string", "null"],
                "description": (
                    "Room/area, only set if the question mentions one, "
                    "e.g. 'kitchen', 'upstairs bath'."
                ),
            },
        },
        "required": ["address_query"],
    },
}

LOOKUP_SYSTEM_PROMPT = """You are Matty, parsing a plain-English question about \
previously logged building materials (e.g. "what paint color did we use at \
Lincoln Heights?") into search parameters.

Call the record_lookup_query tool with the result. Do not include any \
commentary outside of the tool call."""


def _extract_lookup_query(question_text: str) -> dict:
    client = _anthropic_client()
    message = client.messages.create(
        model=_model(),
        max_tokens=512,
        system=LOOKUP_SYSTEM_PROMPT,
        tools=[LOOKUP_QUERY_TOOL],
        tool_choice={"type": "tool", "name": "record_lookup_query"},
        messages=[{"role": "user", "content": question_text}],
    )
    tool_use = next(block for block in message.content if block.type == "tool_use")
    return tool_use.input


def _format_record(fields: dict) -> str:
    parts = []
    if fields.get("Brand / Manufacturer"):
        parts.append(f"<b>{html.escape(fields['Brand / Manufacturer'])}</b>")
    if fields.get("Item Name & Description"):
        parts.append(html.escape(fields["Item Name & Description"]))
    if fields.get("Color / Finish / Size"):
        parts.append(html.escape(fields["Color / Finish / Size"]))
    line = " — ".join(parts) if parts else "(no details recorded)"
    extras = []
    if fields.get("Model / SKU / Barcode"):
        extras.append(f"SKU: {html.escape(fields['Model / SKU / Barcode'])}")
    if fields.get("Location in House"):
        extras.append(html.escape(fields["Location in House"]))
    if extras:
        line += f" ({', '.join(extras)})"
    return line


def answer_lookup_question(question_text: str) -> str:
    """Parses the question, searches Airtable, and returns a Telegram-
    ready HTML reply. Never raises."""
    try:
        query = _extract_lookup_query(question_text)
    except Exception as e:
        return f"I couldn't quite parse that — {html.escape(str(e))}"

    records = search_material_records(
        address_query=query.get("address_query"),
        category=query.get("category"),
        location=query.get("location"),
    )
    if not records:
        return "I've no record of that in the materials log — perhaps it hasn't been logged yet."

    lines = [_format_record(r.get("fields", {})) for r in records]
    if len(lines) == 1:
        return f"Found it: {lines[0]}"
    body = "\n".join(f"  • {line}" for line in lines)
    return f"Found {len(lines)} matching entries:\n{body}"
