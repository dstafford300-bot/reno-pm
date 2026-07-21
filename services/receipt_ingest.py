from supabase import Client

from services.db_writer import create_material_log
from services.receipt_parser import extract_amount_hint
from services.storage import upload_receipt_photo
from services.telegram_bot import download_file_bytes

RECEIPT_KEYWORDS = ("jeeves receipt", "jeeves material")


def is_receipt_message(message: dict) -> bool:
    """A journal-message-shaped dict (from fetch_journal_messages) counts
    as a receipt if it has a photo AND its text/caption contains one of the
    trigger keywords."""
    if not message.get("photo_file_id"):
        return False
    text = (message.get("message_text") or "").strip().lower()
    return any(keyword in text for keyword in RECEIPT_KEYWORDS)


def process_receipt_message(
    supabase: Client, property_id: str, message: dict
) -> dict | None:
    """Downloads the photo, re-hosts it in Supabase Storage, pulls a dollar
    amount out of the caption if present, and logs a material_logs row.
    Returns the created row, or None if the photo couldn't be downloaded."""
    image_bytes = download_file_bytes(message["photo_file_id"])
    if image_bytes is None:
        return None

    photo_url = upload_receipt_photo(supabase, image_bytes)
    caption = message.get("message_text") or ""
    amount = extract_amount_hint(caption) or 0

    return create_material_log(
        supabase,
        store="Telegram Receipt",
        amount=amount,
        property_id=property_id,
        purchase_date=message.get("posted_at"),
        receipt_details=caption,
        photo_url=photo_url,
        source="telegram",
    )
