"""Copies Telegram media into our own private storage when it arrives, so it
doesn't depend on Telegram keeping the file."""

import mimetypes

from supabase import Client

from services.storage import upload_journal_media
from services.telegram_bot import download_file_bytes

_DEFAULTS = {
    "photo": ("image/jpeg", "jpg"),
    "voice": ("audio/ogg", "ogg"),
    "video": ("video/mp4", "mp4"),
    "video_note": ("video/mp4", "mp4"),
    "audio": ("audio/mpeg", "mp3"),
    "animation": ("video/mp4", "mp4"),
    "document": ("application/octet-stream", "bin"),
}


def file_id_of(message: dict, kind: str) -> str | None:
    if kind == "photo":
        sizes = message.get("photo") or []
        return sizes[-1]["file_id"] if sizes else None
    return (message.get(kind) or {}).get("file_id")


def copy_to_storage(
    supabase: Client, message: dict, kind: str, folder: str
) -> tuple[str | None, str | None]:
    """(storage_path, note). The note explains a failure — Telegram only lets
    bots download files up to 20 MB — so it can be shown instead of silently
    dropping the file."""
    file_id = file_id_of(message, kind)
    if not file_id:
        return None, None
    data = download_file_bytes(file_id)
    if data is None:
        return None, "Couldn't be saved (over Telegram's 20 MB limit, or unavailable)"

    content_type, extension = _DEFAULTS.get(kind, _DEFAULTS["document"])
    meta = message.get(kind) if isinstance(message.get(kind), dict) else {}
    if meta.get("mime_type"):
        content_type = meta["mime_type"]
        extension = (mimetypes.guess_extension(content_type) or f".{extension}").lstrip(".")
    if meta.get("file_name") and "." in meta["file_name"]:
        extension = meta["file_name"].rsplit(".", 1)[1][:8]
    try:
        return upload_journal_media(supabase, data, content_type, folder, extension), None
    except Exception as e:
        return None, f"Couldn't be saved: {e}"
