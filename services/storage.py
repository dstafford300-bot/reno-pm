import uuid

from supabase import Client

RECEIPTS_BUCKET = "receipts"
LEGACY_MATERIALS_BUCKET = "legacy-materials"


def _ensure_bucket(supabase: Client, bucket_name: str) -> None:
    """Create a public storage bucket if it doesn't already exist. Safe to
    call repeatedly — no-ops if it's already there."""
    buckets = supabase.storage.list_buckets()
    if not any(b.name == bucket_name for b in buckets):
        supabase.storage.create_bucket(bucket_name, options={"public": True})


def ensure_receipts_bucket(supabase: Client) -> None:
    _ensure_bucket(supabase, RECEIPTS_BUCKET)


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


def upload_legacy_material_photo(
    supabase: Client, image_bytes: bytes, content_type: str = "image/jpeg"
) -> str:
    """Uploads a legacy-material label photo and returns its permanent
    public URL — Airtable's attachment field fetches from this URL when
    the record is created, so it must be public and stable (not a
    short-lived Telegram file URL)."""
    _ensure_bucket(supabase, LEGACY_MATERIALS_BUCKET)
    path = f"{uuid.uuid4()}.jpg"
    supabase.storage.from_(LEGACY_MATERIALS_BUCKET).upload(
        path, image_bytes, {"content-type": content_type}
    )
    return supabase.storage.from_(LEGACY_MATERIALS_BUCKET).get_public_url(path)
