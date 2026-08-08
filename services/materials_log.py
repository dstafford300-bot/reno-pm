"""Legacy building materials log: send Jeeves a photo of a material label
(paint can, tile box, plumbing part) with a caption starting with the
LEGACY_TRIGGER phrase, and it gets OCR'd/parsed via Claude Vision and
written to the "Legacy Materials Log" Airtable base. Also handles plain-
English lookup questions ("Jeeves, what paint color did we use at Lincoln
Heights?") against the same base.

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

LEGACY_TRIGGER = "jeeves legacy"

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
                    "'Agreeable Gray, Eggshell, 1 Gallon'."
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
                    "map into the other fields."
                ),
            },
        },
        "required": ["property_address", "location_in_house", "category"],
    },
}

EXTRACT_SYSTEM_PROMPT = """You are Jeeves, extracting a legacy building material \
record from a photo of a product label (paint can, tile box, plumbing part, etc.) \
and a short accompanying message from the property manager.

Read the label photo carefully for brand, item description, color/finish/size, \
and any model/SKU/barcode number. Read the message text for the property address, \
room/location, and category — infer the category from the item itself if the \
message doesn't state one.

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


def log_material_from_message(
    image_bytes: bytes, caption_text: str, photo_public_url: str
) -> dict:
    """Extracts fields, writes the Airtable record, and returns
    {"success": bool, "confirmation_text": str}. Never raises — callers
    are Telegram webhook handlers that should always be able to reply
    with *something*, even on failure."""
    try:
        parsed = extract_material_fields(image_bytes, caption_text)
    except Exception as e:
        return {
            "success": False,
            "confirmation_text": (
                f"🎩 I regret I couldn't read that label, sir — "
                f"{html.escape(str(e))}"
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
        "Photo": [{"url": photo_public_url}],
        "Project Type": parsed.get("project_type") or DEFAULT_PROJECT_TYPE,
        "Notes": parsed.get("notes"),
    }
    fields = {k: v for k, v in fields.items() if v not in (None, "")}

    record = create_material_record(fields)
    if not record:
        return {
            "success": False,
            "confirmation_text": (
                "🎩 I read the label but couldn't reach the materials log, "
                "sir — please check the Airtable connection."
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
            f"🎩 Logged, sir: {html.escape(what)} under "
            f"{html.escape(location_label)}."
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

LOOKUP_SYSTEM_PROMPT = """You are Jeeves, parsing a plain-English question about \
previously logged building materials (e.g. "what paint color did we use at \
Lincoln Heights?") into search parameters.

Call the record_lookup_query tool with the result. Do not include any \
commentary outside of the tool call."""


def is_lookup_question(text: str) -> bool:
    return (text or "").strip().lower().startswith("jeeves")


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
        return f"🎩 I couldn't quite parse that, sir — {html.escape(str(e))}"

    records = search_material_records(
        address_query=query.get("address_query"),
        category=query.get("category"),
        location=query.get("location"),
    )
    if not records:
        return (
            "🎩 I've no record of that in the materials log, sir — "
            "perhaps it hasn't been logged yet."
        )

    lines = [_format_record(r.get("fields", {})) for r in records]
    if len(lines) == 1:
        return f"🎩 Indeed, sir: {lines[0]}"
    body = "\n".join(f"  • {line}" for line in lines)
    return f"🎩 I found {len(lines)} matching entries, sir:\n{body}"
