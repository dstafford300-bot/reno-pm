import uuid

from supabase import Client

RECEIPTS_BUCKET = "receipts"


def ensure_receipts_bucket(supabase: Client) -> None:
    """Create the receipts bucket if it doesn't already exist. Safe to call
    repeatedly — no-ops if it's already there."""
    buckets = supabase.storage.list_buckets()
    if not any(b.name == RECEIPTS_BUCKET for b in buckets):
        supabase.storage.create_bucket(RECEIPTS_BUCKET, options={"public": True})


def upload_receipt_photo(
    supabase: Client, image_bytes: bytes, content_type: str = "image/jpeg"
) -> str:
    """Uploads a receipt photo to the receipts bucket and returns its
    permanent public URL."""
    ensure_receipts_bucket(supabase)
    path = f"{uuid.uuid4()}.jpg"
    supabase.storage.from_(RECEIPTS_BUCKET).upload(
        path, image_bytes, {"content-type": content_type}
    )
    return supabase.storage.from_(RECEIPTS_BUCKET).get_public_url(path)
