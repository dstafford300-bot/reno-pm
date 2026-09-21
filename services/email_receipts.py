"""Gmail IMAP receipt scraper. Uses stdlib imaplib/email only — no new
dependency. Runs as part of the same scheduled nightly job as the Telegram
journal sync (see scripts/nightly_journal_sync.py), not a separate
always-on process — consistent with the rest of this app's "scheduled
background job" pattern rather than a persistent thread.
"""

import email
import html as html_module
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message

import imaplib

from supabase import Client

from services.db_writer import check_unit_overrun, create_material_log
from services.receipt_parser import (
    NotAReceiptError,
    match_property_from_text,
    match_unit_from_reference,
    parse_receipt_pdf,
    parse_receipt_text,
)
from services.telegram_bot import send_cost_overrun_alert
from utils.settings import get_setting

IMAP_SERVER = "imap.gmail.com"
RECEIPT_SENDERS = ["homedepot", "lowes"]

# All Mail, not INBOX: a Gmail filter files Home Depot receipts under a
# label and out of the inbox, so an INBOX-only search never sees them.
# Opened read-only, and only messages whose subject says "receipt" are
# considered — a bare FROM match also catches marketing/perks emails,
# which the body-text parser once turned into junk purchase records.
SUBJECT_KEYWORD = "receipt"
_LIST_LINE_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+"[^"]*"\s+(?P<name>.+)$')

# Looks at recent mail regardless of read/unread — a Gmail filter that
# auto-marks receipts read (and labels them) would otherwise hide every one
# from an UNSEEN-only search. Dedupe is by Message-ID in processed_emails
# instead of by mutating the mailbox's read state.
LOOKBACK_DAYS = 30
_IMAP_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

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


def _find_all_mail_mailbox(imap: imaplib.IMAP4_SSL) -> str:
    """The name of Gmail's "All Mail" folder, found via its \\All special-
    use flag rather than by name — it's "[Gmail]/All Mail" for most
    accounts but "[Google Mail]/All Mail" on others (regional accounts),
    and a hardcoded name silently fell back to the inbox. Falls back to
    INBOX if no folder carries the flag."""
    try:
        status, lines = imap.list()
        if status == "OK":
            for line in lines or []:
                match = _LIST_LINE_RE.search(line or b"")
                if match and b"\\All" in match.group("flags"):
                    name = match.group("name").decode().strip()
                    return name if name.startswith('"') else f'"{name}"'
    except Exception:
        pass
    return "INBOX"


def _imap_date(d: date) -> str:
    # Built by hand rather than strftime("%b") — IMAP requires English
    # month abbreviations regardless of the machine's locale.
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


def _extract_pdf_attachments(msg: Message) -> list[bytes]:
    pdfs = []
    for part in msg.walk():
        filename = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                pdfs.append(payload)
    return pdfs


def _decoded_subject(msg: Message) -> str:
    try:
        return str(make_header(decode_header(msg.get("Subject", ""))))
    except Exception:
        return msg.get("Subject", "") or ""


def _processed_message_ids(supabase: Client) -> set[str] | None:
    """None (not an empty set) when the processed_emails table can't be
    read — the caller must then skip the whole sync rather than proceed
    without dedupe, since a missing table would otherwise re-import the
    whole lookback window as duplicate purchase records every night."""
    try:
        rows = supabase.table("processed_emails").select("message_id").execute().data
    except Exception:
        return None
    return {row["message_id"] for row in rows}


def _mark_processed(supabase: Client, message_id: str) -> None:
    try:
        supabase.table("processed_emails").insert({"message_id": message_id}).execute()
    except Exception as e:
        print(f"  WARNING: couldn't record {message_id} as processed: {e}")


def _log_one_email(supabase: Client, msg: Message, properties: list[dict]) -> bool:
    """Parses one receipt email, writes its material_logs row, and fires
    any budget-overrun alert. Returns True if the purchase ended up
    assigned to a property. Raises NotAReceiptError if it isn't a readable
    receipt, or any other exception on a transient failure."""
    body = _extract_plain_text_body(msg)
    pdfs = _extract_pdf_attachments(msg)

    if pdfs:
        # Home Depot puts the whole receipt in a PDF attachment; the body
        # is only a thank-you note with an order number.
        parsed = parse_receipt_pdf(pdfs[0])
        reference = parsed.get("job_or_property_reference") or ""
        item_lines = "\n".join(
            f"{li.get('description')} ${li.get('cost', 0):,.2f}"
            for li in parsed.get("line_items") or []
        )
        match_text = f"{body}\n{reference}"
        receipt_details = f"{_decoded_subject(msg)}\n{reference}\n{item_lines}"[:5000]
    elif body.strip():
        parsed = parse_receipt_text(body)
        reference = parsed.get("job_or_property_reference") or ""
        match_text = body
        receipt_details = body[:5000]
    else:
        raise NotAReceiptError("no body text and no PDF attachment")

    property_id = match_property_from_text(match_text, properties)

    # The unit comes from the job name printed on the receipt ("809 Fred
    # Unit 3 kitchen") — contractors write it at checkout. No unit named
    # means it stays unassigned for the PM to file, never a guess.
    unit_id = None
    if property_id:
        try:
            units = (
                supabase.table("units")
                .select("id, unit_name")
                .eq("property_id", property_id)
                .execute()
                .data
            )
            unit_id = match_unit_from_reference(reference, units)
        except Exception:
            pass  # unit matching is best-effort, never blocks the log

    create_material_log(
        supabase,
        store=parsed.get("store_name", "Unknown"),
        amount=parsed["total_cost"],
        property_id=property_id,
        purchase_date=parsed.get("purchase_date"),
        receipt_details=receipt_details,
        source="email",
        line_items_json=parsed.get("line_items"),
        unit_id=unit_id,
    )

    if unit_id:
        try:
            overrun = check_unit_overrun(supabase, unit_id)
            if overrun:
                send_cost_overrun_alert(
                    property_name=overrun["property_name"],
                    unit_name=overrun["unit_name"],
                    budgeted_cost=overrun["budgeted_cost"],
                    spent=overrun["spent"],
                    percent=overrun["percent"],
                    chat_id=overrun["chat_id"],
                )
        except Exception:
            pass  # a missed overrun alert shouldn't fail the sync job

    return property_id is not None


def sync_email_receipts(supabase: Client, properties: list[dict]) -> dict:
    """Connects to Gmail via IMAP, finds Home Depot / Lowe's emails from
    the last LOOKBACK_DAYS (read or unread), parses each new one as a
    receipt via Claude — reading the PDF attachment when there is one —
    auto-maps it to a property, and inserts a material_logs row. Emails
    are deduped by Message-ID (processed_emails table); the mailbox
    itself is never modified.

    One bad email never stops the rest: a non-receipt (marketing, or
    anything with no readable total) is recorded as processed so it isn't
    retried, while a transient failure (API/network) leaves it
    unprocessed to retry on the next run.

    Returns {"found": n, "processed": n, "unassigned": n}. Silently
    returns all-zeros if EMAIL_USER/EMAIL_PASSWORD aren't configured, the
    IMAP connection fails outright, or processed_emails doesn't exist yet
    — this is a best-effort background sync, not something that should
    crash a scheduled job.
    """
    user = get_setting("EMAIL_USER")
    password = get_setting("EMAIL_PASSWORD")
    if not user or not password:
        return {"found": 0, "processed": 0, "unassigned": 0}

    processed_ids = _processed_message_ids(supabase)
    if processed_ids is None:
        print(
            "  Email: skipped — processed_emails table missing "
            "(run scripts/migration_processed_emails.sql)"
        )
        return {"found": 0, "processed": 0, "unassigned": 0}

    found = processed = unassigned = 0

    try:
        imap = imaplib.IMAP4_SSL(IMAP_SERVER)
        imap.login(user, password)
        status, _ = imap.select(_find_all_mail_mailbox(imap), readonly=True)
        if status != "OK":
            imap.select("INBOX", readonly=True)
    except Exception:
        return {"found": 0, "processed": 0, "unassigned": 0}

    since = _imap_date(date.today() - timedelta(days=LOOKBACK_DAYS))
    try:
        uids_seen: set[bytes] = set()
        for sender in RECEIPT_SENDERS:
            status, data = imap.search(
                None,
                "SINCE", since,
                "FROM", f'"{sender}"',
                "SUBJECT", f'"{SUBJECT_KEYWORD}"',
            )
            if status != "OK" or not data or not data[0]:
                continue

            for uid in data[0].split():
                if uid in uids_seen:
                    continue
                uids_seen.add(uid)

                # BODY.PEEK so reading never flips the message to \\Seen.
                status, msg_data = imap.fetch(uid, "(BODY.PEEK[])")
                if (
                    status != "OK"
                    or not msg_data
                    or not isinstance(msg_data[0], tuple)
                ):
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                message_id = (msg.get("Message-ID") or "").strip()
                if not message_id or message_id in processed_ids:
                    continue
                found += 1

                try:
                    assigned = _log_one_email(supabase, msg, properties)
                except NotAReceiptError:
                    _mark_processed(supabase, message_id)
                    continue
                except Exception as e:
                    print(f"  WARNING: {_decoded_subject(msg)!r} failed, will retry: {e}")
                    continue

                _mark_processed(supabase, message_id)
                processed += 1
                if not assigned:
                    unassigned += 1
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return {"found": found, "processed": processed, "unassigned": unassigned}
