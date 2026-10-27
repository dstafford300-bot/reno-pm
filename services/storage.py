"""Supabase Storage helpers. Every bucket is PRIVATE: files are only ever
reachable through short-lived signed links, never a permanent public URL
(receipts and site photos aren't meant for anyone who happens to find a link).
"""

import uuid

from supabase import Client

RECEIPTS_BUCKET = "receipts"
LEGACY_MATERIALS_BUCKET = "legacy-materials"
JOURNAL_MEDIA_BUCKET = "journal-media"

# A material_logs.photo_url holding this prefix is a private-storage
# reference ("storage://bucket/path"); older rows hold a full public URL.
_REF_PREFIX = "storage://"
_PUBLIC_MARKER = "/object/public/"


def _ensure_private_bucket(supabase: Client, bucket_name: str) -> None:
    """Creates the bucket private, or flips an existing public one to
    private. Safe to call repeatedly."""
    buckets = {b.name: b for b in supabase.storage.list_buckets()}
    existing = buckets.get(bucket_name)
    if existing is None:
        supabase.storage.create_bucket(bucket_name, options={"public": False})
    elif existing.public:
        supabase.storage.update_bucket(bucket_name, {"public": False})


def _signed(result) -> str | None:
    if isinstance(result, dict):
        return result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
    return None


def signed_url(supabase: Client, bucket: str, path: str, seconds: int = 3600) -> str | None:
    try:
        return _signed(supabase.storage.from_(bucket).create_signed_url(path, seconds))
    except Exception:
        return None


def signed_urls(
    supabase: Client, bucket: str, paths: list[str], seconds: int = 3600
) -> dict[str, str]:
    """{path: signed_url} for every path that could be signed, in one call."""
    if not paths:
        return {}
    try:
        rows = supabase.storage.from_(bucket).create_signed_urls(paths, seconds)
    except Exception:
        return {}
    out = {}
    for row in rows:
        url = _signed(row)
        if url and row.get("path"):
            out[row["path"]] = url
    return out


def resolve_photo_ref(supabase: Client, value: str | None, seconds: int = 3600) -> str | None:
    """Turns a stored photo reference into a viewable signed URL. Accepts
    the new "storage://bucket/path" form and legacy public URLs (whose bucket
    and path are recovered from the URL, since the bucket is now private)."""
    if not value:
        return None
    if value.startswith(_REF_PREFIX):
        bucket, _, path = value[len(_REF_PREFIX):].partition("/")
    elif _PUBLIC_MARKER in value:
        bucket, _, path = value.split(_PUBLIC_MARKER, 1)[1].partition("/")
    else:
        return value
    return signed_url(supabase, bucket, path.split("?")[0], seconds)


def upload_receipt_photo(
    supabase: Client, image_bytes: bytes, content_type: str = "image/jpeg"
) -> str:
    """Stores a receipt photo and returns a "storage://" reference to keep in
    material_logs.photo_url (resolve with resolve_photo_ref to view it)."""
    _ensure_private_bucket(supabase, RECEIPTS_BUCKET)
    path = f"{uuid.uuid4()}.jpg"
    supabase.storage.from_(RECEIPTS_BUCKET).upload(
        path, image_bytes, {"content-type": content_type}
    )
    return f"{_REF_PREFIX}{RECEIPTS_BUCKET}/{path}"


def upload_legacy_material_photo(
    supabase: Client, image_bytes: bytes, content_type: str = "image/jpeg"
) -> str:
    """Stores a legacy-material label photo and returns a SHORT-LIVED signed
    URL — Airtable fetches the attachment from it at record creation and
    keeps its own copy, so the link only has to work for a few minutes."""
    _ensure_private_bucket(supabase, LEGACY_MATERIALS_BUCKET)
    path = f"{uuid.uuid4()}.jpg"
    supabase.storage.from_(LEGACY_MATERIALS_BUCKET).upload(
        path, image_bytes, {"content-type": content_type}
    )
    return signed_url(supabase, LEGACY_MATERIALS_BUCKET, path, 900) or ""


def upload_journal_media(
    supabase: Client, data: bytes, content_type: str, folder: str, extension: str
) -> str:
    """Copies a Telegram photo/voice note/video into private storage so it
    survives Telegram dropping the file. Returns the path inside the bucket."""
    _ensure_private_bucket(supabase, JOURNAL_MEDIA_BUCKET)
    path = f"{folder}/{uuid.uuid4()}.{extension}"
    supabase.storage.from_(JOURNAL_MEDIA_BUCKET).upload(
        path, data, {"content-type": content_type}
    )
    return path
