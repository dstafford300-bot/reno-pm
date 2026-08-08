"""Thin wrapper around the Airtable REST API for the Legacy Materials Log
base. Deliberately not using the Airtable Python SDK — one small wrapper
around `requests` keeps this consistent with the rest of the app (Telegram,
Supabase-adjacent services) and avoids one more dependency for two
endpoints.
"""

import os

import requests

AIRTABLE_API_BASE = "https://api.airtable.com/v0"
MATERIALS_BASE_ID = "appLPnhEfnvEKpnCx"
MATERIALS_TABLE_ID = "tblxxiwaMiTIdM9Ps"


def _get_api_key() -> str | None:
    return os.environ.get("AIRTABLE_API_KEY")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


def create_material_record(fields: dict) -> dict | None:
    """Creates one record in the Materials table. `fields` keys must match
    the table's actual field names exactly (e.g. "Property Address",
    "Category"). Returns the created record dict, or None on any failure
    — never raises, since this is called from a webhook handler that
    should reply with a clear error to Telegram rather than crash."""
    api_key = _get_api_key()
    if not api_key:
        return None
    try:
        response = requests.post(
            f"{AIRTABLE_API_BASE}/{MATERIALS_BASE_ID}/{MATERIALS_TABLE_ID}",
            headers=_headers(),
            json={"fields": fields},
            timeout=20,
        )
        if not response.ok:
            return None
        return response.json()
    except requests.RequestException:
        return None


def _escape_formula_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def search_material_records(
    address_query: str | None = None,
    category: str | None = None,
    location: str | None = None,
    max_records: int = 10,
) -> list[dict]:
    """Case-insensitive substring search against Property Address (always),
    optionally narrowed by an exact Category match and a Location in House
    substring. Returns a list of {"fields": {...}} records, most-recently
    -created first. Returns [] on any failure or if no filters produced a
    usable query."""
    api_key = _get_api_key()
    if not api_key or not address_query:
        return []

    clauses = [
        f'SEARCH("{_escape_formula_string(address_query.lower())}", '
        f'LOWER({{Property Address}}))'
    ]
    if category:
        clauses.append(f'{{Category}} = "{_escape_formula_string(category)}"')
    if location:
        clauses.append(
            f'SEARCH("{_escape_formula_string(location.lower())}", '
            f'LOWER({{Location in House}}))'
        )
    formula = "AND(" + ", ".join(clauses) + ")" if len(clauses) > 1 else clauses[0]

    try:
        response = requests.get(
            f"{AIRTABLE_API_BASE}/{MATERIALS_BASE_ID}/{MATERIALS_TABLE_ID}",
            headers=_headers(),
            params={
                "filterByFormula": formula,
                "maxRecords": max_records,
                "sort[0][field]": "Property Address",
            },
            timeout=20,
        )
        if not response.ok:
            return []
        return response.json().get("records", [])
    except requests.RequestException:
        return []
