from datetime import date

from utils.anthropic_client import get_anthropic_client, get_model

SOW_TOOL = {
    "name": "record_sow_data",
    "description": (
        "Record the structured scope-of-work data extracted from a raw "
        "renovation spreadsheet."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "properties": {
                "type": "array",
                "description": "Distinct properties/buildings found in the sheet.",
                "items": {
                    "type": "object",
                    "properties": {
                        "property_name": {
                            "type": "string",
                            "description": "e.g. '809 Fred Shuttlesworth'",
                        },
                        "address": {"type": "string"},
                        "units": {
                            "type": "array",
                            "description": "Sub-units/projects within this property.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "unit_name": {"type": "string"},
                                    "line_items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "task_name": {"type": "string"},
                                                "cost_group": {
                                                    "type": "string",
                                                    "description": (
                                                        "Trade category, e.g. Demo, "
                                                        "Framing, Plumbing, Electrical, "
                                                        "Drywall, Flooring, Paint, "
                                                        "Fixtures, Cleanup."
                                                    ),
                                                },
                                                "budgeted_cost": {"type": "number"},
                                                "start_date": {
                                                    "type": "string",
                                                    "description": "YYYY-MM-DD",
                                                },
                                                "estimated_end_date": {
                                                    "type": "string",
                                                    "description": "YYYY-MM-DD",
                                                },
                                                "notes": {"type": "string"},
                                            },
                                            "required": ["task_name", "budgeted_cost"],
                                        },
                                    },
                                },
                                "required": ["unit_name", "line_items"],
                            },
                        },
                    },
                    "required": ["property_name", "units"],
                },
            },
        },
        "required": ["properties"],
    },
}

SYSTEM_PROMPT = """You are a construction project management assistant. You will be \
given the raw, unstandardized contents of a real estate renovation scope-of-work \
(SOW) spreadsheet export (one or more sheets, dumped as CSV grids with no assumed \
header row).

The layout is NOT standardized — properties, sub-units, and line items may appear as \
rows, columns, section headers, merged-looking blocks, or across multiple sheets. \
Analyze the layout dynamically rather than assuming a fixed schema. Your job:

1. Identify each distinct PROPERTY (a separate building/address), for example \
"809 Fred Shuttlesworth" and "811 Fred Shuttlesworth" are two different properties, \
even if they appear in the same sheet or share a street name.
2. Within each property, identify its UNITS (sub-projects — e.g. "Unit A", \
"Basement", "Exterior", "Whole House" if there is no further breakdown).
3. Within each unit, extract every individual LINE ITEM task with its budgeted cost. \
Assign each a cost_group using standard residential renovation trade categories \
(Demo, Framing, Plumbing, Electrical, HVAC, Drywall, Flooring, Paint, Fixtures, \
Cleanup, etc.) based on the task description.
4. Infer start_date and estimated_end_date for every line item by sequencing tasks \
in standard construction order of operations — permits and demo first, then \
structural/framing, then rough-in trades (plumbing/electrical/HVAC), then drywall, \
then finishes (flooring, paint, fixtures), then punch list/cleanup last. Assume the \
project starts on {today} unless the sheet states otherwise, and give each task a \
realistic duration in days for its scope, sequencing dependent trades back-to-back \
within the same unit.

Call the record_sow_data tool with the complete structured result. Do not include \
any commentary outside of the tool call."""


def parse_sow(raw_text: str) -> dict:
    """Send raw spreadsheet text to Claude and return the structured
    {properties: [{units: [{line_items: [...]}]}]} payload."""
    client = get_anthropic_client()
    model = get_model()

    message = client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        tools=[SOW_TOOL],
        tool_choice={"type": "tool", "name": "record_sow_data"},
        messages=[
            {
                "role": "user",
                "content": f"Raw spreadsheet data:\n\n{raw_text}",
            }
        ],
    )

    tool_use = next(
        block for block in message.content if block.type == "tool_use"
    )
    return tool_use.input
