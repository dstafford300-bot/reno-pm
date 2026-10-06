import io

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from supabase import Client

from api.security import owner_only
from db.connection import get_supabase_client
from services.claude_parser import parse_sow, parse_sow_from_pdf
from services.db_writer import save_parsed_sow

router = APIRouter(prefix="/api/sow", tags=["sow"])

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS_PER_SHEET = 500


def _spreadsheet_text(name: str, data: bytes) -> str:
    """Raw CSV-grid text with no assumed header row, so Claude can read
    whatever layout the sheet actually uses."""
    buffer = io.BytesIO(data)
    if name.endswith(".csv"):
        return pd.read_csv(buffer, header=None).head(MAX_ROWS_PER_SHEET).to_csv(
            index=False, header=False
        )
    sheets = pd.read_excel(buffer, sheet_name=None, header=None)
    return "\n\n".join(
        f"### Sheet: {sheet}\n{df.head(MAX_ROWS_PER_SHEET).to_csv(index=False, header=False)}"
        for sheet, df in sheets.items()
    )


@router.post("/parse")
async def parse_upload(file: UploadFile, user: dict = Depends(owner_only)):
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xls", ".csv", ".pdf")):
        raise HTTPException(422, "Upload an Excel, CSV or PDF scope of work")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "That file is too large (25 MB max)")
    try:
        parsed = parse_sow_from_pdf(data) if name.endswith(".pdf") else parse_sow(
            _spreadsheet_text(name, data)
        )
    except Exception as e:
        raise HTTPException(422, f"Couldn't read that scope of work: {e}")
    return {"parsed": parsed}


class ImportBody(BaseModel):
    parsed: dict


@router.post("/import")
def import_parsed(
    body: ImportBody,
    user: dict = Depends(owner_only),
    db: Client = Depends(get_supabase_client),
):
    if not body.parsed.get("properties"):
        raise HTTPException(422, "Nothing to import")
    return save_parsed_sow(db, body.parsed)
