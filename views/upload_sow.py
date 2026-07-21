import io

import pandas as pd
import streamlit as st

from db.connection import get_supabase_client
from services.claude_parser import parse_sow
from services.db_writer import save_parsed_sow

MAX_ROWS_PER_SHEET = 500


def _extract_raw_text(uploaded_file) -> str:
    """Dump the uploaded file into raw CSV-grid text, with no assumed header
    row, so Claude can interpret whatever layout the sheet actually uses."""
    name = uploaded_file.name.lower()
    # Materialize into a plain BytesIO buffer — pandas/openpyxl seek around
    # the stream to find the zip central directory, and Streamlit's
    # UploadedFile wrapper doesn't always support that reliably.
    buffer = io.BytesIO(uploaded_file.getvalue())

    if name.endswith(".csv"):
        df = pd.read_csv(buffer, header=None).head(MAX_ROWS_PER_SHEET)
        return df.to_csv(index=False, header=False)

    sheets = pd.read_excel(buffer, sheet_name=None, header=None)
    parts = []
    for sheet_name, df in sheets.items():
        parts.append(
            f"### Sheet: {sheet_name}\n"
            f"{df.head(MAX_ROWS_PER_SHEET).to_csv(index=False, header=False)}"
        )
    return "\n\n".join(parts)


def render():
    st.title("Upload SOW")
    st.caption(
        "Upload a raw scope-of-work spreadsheet. Claude identifies the "
        "properties, units, and line items, then populates the database "
        "automatically."
    )

    uploaded_file = st.file_uploader(
        "Excel or CSV Scope of Work", type=["xlsx", "xls", "csv"]
    )

    if uploaded_file is None:
        return

    if not st.button("Parse & Import", type="primary"):
        return

    with st.spinner("Reading spreadsheet..."):
        raw_text = _extract_raw_text(uploaded_file)

    with st.spinner("Asking Claude to structure the data..."):
        try:
            parsed = parse_sow(raw_text)
        except Exception as e:
            st.error(f"Claude parsing failed: {e}")
            return

    st.subheader("Parsed preview")
    st.json(parsed)

    with st.spinner("Writing to Supabase..."):
        try:
            supabase = get_supabase_client()
            summary = save_parsed_sow(supabase, parsed)
        except Exception as e:
            st.error(f"Database write failed: {e}")
            return

    st.success(
        f"Imported {summary['properties']} propert(y/ies), "
        f"{summary['units']} unit(s), {summary['line_items']} line item(s)."
    )
