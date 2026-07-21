"""Gmail IMAP receipt scraper. Uses stdlib imaplib/email only — no new
dependency. Runs as part of the same scheduled nightly job as the Telegram
journal sync (see scripts/nightly_journal_sync.py), not a separate
always-on process — consistent with the rest of this app's "scheduled
background job" pattern rather than a persistent thread.
"""

import email
import html as html_module
import re
from email.message import Message

import imaplib

from supabase import Client

from services.db_writer import create_material_log
from services.receipt_parser import match_property_from_text, parse_receipt_text
from utils.settings import get_setting

IMAP_SERVER = "imap.gmail.com"
RECEIPT_SENDERS = ["homedepot", "lowes"]

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def _html_to_text(raw_html: str) -> str:
    """Strip an HTML email body down to plain text — Home Depot/Lowe's
    receipt emails are frequently HTML-only with no text/plain part, so
    this can't just be skipped."""
    no_scripts = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    no_tags = _TAG_RE.sub("\n", no_scripts)
    unescaped = html_module.unescape(no_tags)
    return _BLANK_LINES_RE.sub("\n\n", unescaped).strip()


def _extract_plain_text_body(msg: Message) -> str:
    if msg.is_multipart():
        html_fallback = ""
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not html_fallback:
                payload = part.get_payload(decode=True)
                if payload:
                    html_fallback = payload.decode(charset, errors="replace")
        return _html_to_text(html_fallback) if html_fallback else ""

    charset = msg.get_content_charset() or "utf-8"
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    text = payload.decode(charset, errors="replace")
    if msg.get_content_type() == "text/html":
        return _html_to_text(text)
    return text


def sync_email_receipts(supabase: Client, properties: list[dict]) -> dict:
    """Connects to Gmail via IMAP, finds UNSEEN emails from Home Depot or
    Lowe's, parses each as a receipt via Claude, auto-maps it to a
    property using the same unique-word matching as the pasted-receipt
    flow, inserts a material_logs row, and only THEN marks the email
    \\Seen — a failure partway through (parsing, DB write) leaves it
    UNSEEN so the next run retries it instead of silently losing it.

    Returns {"found": n, "processed": n, "unassigned": n}. Silently
    returns all-zeros if EMAIL_USER/EMAIL_PASSWORD aren't configured, or
    if the IMAP connection fails outright — this is a best-effort
    background sync, not something that should crash a scheduled job.
    """
    user = get_setting("EMAIL_USER")
    password = get_setting("EMAIL_PASSWORD")
    if not user or not password:
        return {"found": 0, "processed": 0, "unassigned": 0}

    found = processed = unassigned = 0

    try:
        imap = imaplib.IMAP4_SSL(IMAP_SERVER)
        imap.login(user, password)
        imap.select("INBOX")
    except Exception:
        return {"found": 0, "processed": 0, "unassigned": 0}

    try:
        uids_seen: set[bytes] = set()
        for sender in RECEIPT_SENDERS:
            status, data = imap.search(None, "UNSEEN", "FROM", f'"{sender}"')
            if status != "OK" or not data or not data[0]:
                continue

            for uid in data[0].split():
                if uid in uids_seen:
                    continue
                uids_seen.add(uid)
                found += 1

                status, msg_data = imap.fetch(uid, "(BODY.PEEK[])")
                if (
                    status != "OK"
                    or not msg_data
                    or not isinstance(msg_data[0], tuple)
                ):
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                body = _extract_plain_text_body(msg)
                if not body.strip():
                    continue

                try:
                    parsed = parse_receipt_text(body)
                except Exception:
                    continue  # leave UNSEEN, retry next run

                property_id = match_property_from_text(body, properties)

                create_material_log(
                    supabase,
                    store=parsed.get("store_name", "Unknown"),
                    amount=parsed.get("total_cost", 0),
                    property_id=property_id,
                    purchase_date=parsed.get("purchase_date"),
                    receipt_details=body[:5000],
                    source="email",
                    line_items_json=parsed.get("line_items"),
                )
                if property_id is None:
                    unassigned += 1
                processed += 1

                # Only mark Seen once the material_log row actually exists.
                imap.store(uid, "+FLAGS", "\\Seen")
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return {"found": found, "processed": processed, "unassigned": unassigned}
