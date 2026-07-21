import html
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from utils.settings import get_setting

# All Jeeves messages use parse_mode="HTML" rather than Telegram's legacy
# "Markdown" — Markdown's *bold*/_italic_ delimiters break the ENTIRE
# message with a 400 if any embedded value (a random access token, a
# query param name, free-text user input) happens to contain an unpaired
# "_" or "*", which is unpredictable and un-debuggable in production.
# HTML only requires escaping &/</>, which every dynamic value below goes
# through via html.escape().

TELEGRAM_API_BASE = "https://api.telegram.org"


def _get_base_url() -> str:
    """Public base URL Jeeves links back to (e.g. a Cloudflare/ngrok tunnel
    once deployed). Falls back to localhost for local-only use. Single
    source of truth — every gantt_url below is built from this rather than
    being passed in by callers, so switching to a real public URL is a
    one-line config change (BASE_URL in .env or secrets.toml)."""
    return get_setting("BASE_URL") or "http://localhost:8501"


def _get_access_token() -> str | None:
    return get_setting("ACCESS_TOKEN")


def _gantt_url(property_id: str) -> str:
    """Secure link back into the client-only Gantt view for one specific
    property — includes the access token (main.py denies every request
    without a match) and view=client, which routes straight past the full
    operational dashboard/sidebar into the read-only subcontractor view.

    Falls back to a plain, unsecured /schedule link if ACCESS_TOKEN isn't
    configured yet, so links don't silently 404 during initial setup.
    """
    base = _get_base_url()
    token = _get_access_token()
    if not token:
        return f"{base}/schedule"
    return (
        f"{base}/?token={quote(token)}&view=client"
        f"&property_id={quote(str(property_id))}"
    )


def _get_bot_token() -> str | None:
    return get_setting("TELEGRAM_BOT_TOKEN")


def _get_default_chat_id() -> str | None:
    """Fallback chat used when a unit has no telegram_chat_id of its own."""
    return get_setting("TELEGRAM_CHAT_ID")


def _closing_remark(new_status: str) -> str:
    lowered = (new_status or "").strip().lower()
    if lowered == "completed":
        return "Splendid progress, indeed. I shall note it in the ledger at once."
    if lowered == "in progress":
        return "The crew presses onward, sir. I shall keep a watchful eye, as ever."
    return "Very good, sir. I await further instruction."


def format_status_alert(
    property_name: str,
    unit_name: str,
    task_name: str,
    old_status: str,
    new_status: str,
    cost: float,
) -> str:
    """Jeeves — an impeccably polite British butler overseeing the site."""
    property_name = html.escape(property_name)
    unit_name = html.escape(unit_name)
    task_name = html.escape(task_name)
    old_status = html.escape(old_status)
    new_status = html.escape(new_status)
    return (
        f"🎩 <b>Good day, sir.</b> Jeeves here, reporting from <b>{property_name}</b>.\n\n"
        f"I have the honour to report a progress update at <i>{unit_name}</i>:\n\n"
        f"🛠️ <b>Task:</b> {task_name}\n"
        f"💷 <b>Estimated Cost:</b> ${cost:,.2f}\n"
        f"📋 <b>Status:</b> {old_status} ➡️ <b>{new_status}</b>\n\n"
        f"{_closing_remark(new_status)}"
    )


def send_telegram_message(chat_id: str | None, text: str) -> bool:
    """Send a message via the Telegram Bot API.

    Returns True on success, False on any failure — missing token, missing
    chat_id, network error, or a non-2xx response from Telegram. Never
    raises, so callers (including background threads) can fire this without
    risking the app crashing on a bad/missing config.
    """
    token = _get_bot_token()
    if not token or not chat_id:
        return False

    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return response.ok
    except requests.RequestException:
        return False


def format_portal_link_message(property_id: str) -> str:
    url = html.escape(_gantt_url(property_id))
    return (
        f'📍 <b>Live Project Portal:</b> <a href="{url}">Tap here for the '
        f"up-to-the-second dynamic Gantt chart and trade sequencing "
        f"layout</a>"
    )


def _send_and_pin_text(text: str, chat_id: str | None) -> bool:
    """Sends `text`, unpins whatever was previously pinned in the chat,
    and pins the new message — so only the latest Jeeves link stays
    pinned instead of accumulating one per publish. Returns True only if
    the send AND the pin succeeded (a failed unpin is not fatal — it just
    means an old link stays pinned alongside the new one); never raises,
    so callers can fire this from a background thread safely.

    Note: unpinAllChatMessages clears every pin in the chat, not just
    ones Jeeves made — fine for a group whose only pinned content is this
    portal link, but would also drop anything a human pinned manually.
    """
    token = _get_bot_token()
    target_chat_id = chat_id or _get_default_chat_id()
    if not token or not target_chat_id:
        return False

    try:
        requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/unpinAllChatMessages",
            json={"chat_id": target_chat_id},
            timeout=10,
        )

        send_response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
            json={"chat_id": target_chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not send_response.ok:
            return False
        message_id = send_response.json().get("result", {}).get("message_id")
        if not message_id:
            return False

        pin_response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/pinChatMessage",
            json={
                "chat_id": target_chat_id,
                "message_id": message_id,
                "disable_notification": True,
            },
            timeout=10,
        )
        return pin_response.ok
    except requests.RequestException:
        return False


def send_and_pin_portal_link(property_id: str, chat_id: str | None = None) -> bool:
    return _send_and_pin_text(format_portal_link_message(property_id), chat_id)


def format_batch_schedule_update_alert(
    property_name: str, property_id: str, changes: list[str]
) -> str:
    """Jeeves' consolidated announcement covering every quick-edit made on
    the Gantt chart since the last publish — one message per batch rather
    than one per edit."""
    change_lines = "\n".join(f"  • {html.escape(c)}" for c in changes)
    url = html.escape(_gantt_url(property_id))
    return (
        f"🎩 <b>Good day, sir.</b> Jeeves here with a consolidated update "
        f"from <b>{html.escape(property_name)}</b>.\n\n"
        f"The following schedule changes have been published:\n"
        f"{change_lines}\n\n"
        f'📊 <a href="{url}">View the live Gantt chart</a>'
    )


def send_and_pin_batch_schedule_update(
    property_name: str,
    property_id: str,
    changes: list[str],
    chat_id: str | None = None,
) -> bool:
    text = format_batch_schedule_update_alert(property_name, property_id, changes)
    return _send_and_pin_text(text, chat_id)


def verification_phrase(property_name: str) -> str:
    """Deterministic sync phrase for a property — recomputed on demand, no
    token storage needed."""
    return f"Jeeves Sync {property_name}"


PM_DIGEST_VERIFICATION_PHRASE = "Jeeves PM Summary"


def format_daily_digest_message(property_summaries: list[dict]) -> str:
    """property_summaries: [{"property_name": ..., "lines": [str, ...]}]
    for properties that had any activity — properties with none are
    omitted entirely rather than shown as an empty section."""
    if not property_summaries:
        return (
            "🎩 <b>Good day, sir.</b> Jeeves here with your daily summary.\n\n"
            "A quiet day across every property — nothing to report."
        )

    sections = []
    for prop in property_summaries:
        lines = "\n".join(f"  • {html.escape(line)}" for line in prop["lines"])
        sections.append(f"<b>{html.escape(prop['property_name'])}</b>\n{lines}")

    body = "\n\n".join(sections)
    return (
        "🎩 <b>Good day, sir.</b> Jeeves here with your daily summary "
        "across all properties.\n\n"
        f"{body}"
    )


def send_daily_digest(text: str, chat_id: str) -> bool:
    return send_telegram_message(chat_id, text)


def get_updates(limit: int = 100, offset: int | None = None) -> list[dict]:
    """Fetch updates Jeeves has received (messages, group-add events, etc).

    Telegram only keeps ~100 *unconfirmed* updates bot-wide (across every
    chat the bot is in) before older ones start falling off — and a call
    with no `offset` never confirms anything, so a busy bot gets stuck
    re-seeing the same oldest batch forever and can never reach anything
    newer. Passing `offset` (one past the highest update_id you've already
    processed) is what advances that window. Callers that just want a
    quick, side-effect-free peek (e.g. group-link verification) can still
    omit it — see services/journal_sync.py for the caller that persists
    and advances a real offset. Returns [] on any failure (missing token,
    network error) rather than raising.
    """
    token = _get_bot_token()
    if not token:
        return []

    params = {"limit": limit}
    if offset is not None:
        params["offset"] = offset

    try:
        response = requests.get(
            f"{TELEGRAM_API_BASE}/bot{token}/getUpdates",
            params=params,
            timeout=10,
        )
        if not response.ok:
            return []
        return response.json().get("result", [])
    except requests.RequestException:
        return []


def _find_chat_by_phrase(phrase: str, allowed_types: tuple[str, ...]) -> dict | None:
    matches = []
    for update in get_updates():
        message = update.get("message") or update.get("channel_post")
        if not message:
            continue
        chat = message.get("chat", {})
        if chat.get("type") not in allowed_types:
            continue
        text = message.get("text") or ""
        if phrase.strip().lower() in text.strip().lower():
            matches.append(
                {
                    "chat_id": chat.get("id"),
                    "chat_title": chat.get("title"),
                    "date": message.get("date", 0),
                }
            )

    if not matches:
        return None
    matches.sort(key=lambda m: m["date"])
    return matches[-1]


def find_group_by_phrase(phrase: str) -> dict | None:
    """Scan recent updates for a group/supergroup message containing
    `phrase` (case-insensitive). Returns the most recent match as
    {"chat_id": ..., "chat_title": ...}, or None if nothing matches."""
    return _find_chat_by_phrase(phrase, ("group", "supergroup"))


def find_private_chat_by_phrase(phrase: str) -> dict | None:
    """Same as find_group_by_phrase, but for a 1-on-1 DM with the bot
    (chat type "private") rather than a group — used to link the PM's
    personal chat for the cross-property daily digest."""
    return _find_chat_by_phrase(phrase, ("private",))


def format_timeline_published_alert(
    property_name: str, property_id: str, tasks: list[dict]
) -> str:
    """Jeeves' announcement when a drafted timeline is published and goes live."""
    phases: dict[str, dict] = {}
    for task in tasks:
        group = task.get("cost_group") or "General"
        start = task.get("start_date")
        end = task.get("estimated_end_date")
        phase = phases.setdefault(group, {"start": start, "end": end})
        if start and (phase["start"] is None or start < phase["start"]):
            phase["start"] = start
        if end and (phase["end"] is None or end > phase["end"]):
            phase["end"] = end

    milestone_lines = "\n".join(
        f"  • <b>{html.escape(group)}:</b> {info['start']} → {info['end']}"
        for group, info in phases.items()
    )
    url = html.escape(_gantt_url(property_id))

    return (
        f"🎩 <b>Good day, sir.</b> Jeeves here with splendid news from "
        f"<b>{html.escape(property_name)}</b>.\n\n"
        f"The draft timeline has been reviewed and committed — the schedule "
        f"is now live and tracking in earnest.\n\n"
        f"📋 <b>Phase Milestones:</b>\n{milestone_lines}\n\n"
        f'📊 <a href="{url}">View the live Gantt chart</a>\n\n'
        f"I shall keep the household informed as work progresses."
    )


def format_schedule_adjustment_alert(
    property_name: str, property_id: str, instruction: str
) -> str:
    """Jeeves' announcement when an active schedule is adjusted (not a
    first-time publish — an incremental change to a live timeline)."""
    url = html.escape(_gantt_url(property_id))
    return (
        f"🎩 <b>Good day, sir.</b> Jeeves here with an update from "
        f"<b>{html.escape(property_name)}</b>.\n\n"
        f"The schedule has been adjusted per your instruction:\n"
        f"<i>{html.escape(instruction)}</i>\n\n"
        f"I have recalculated the downstream dates accordingly.\n\n"
        f'📊 <a href="{url}">View the updated Gantt chart</a>'
    )


def send_schedule_adjustment_alert(
    property_name: str,
    property_id: str,
    instruction: str,
    chat_id: str | None = None,
) -> bool:
    target_chat_id = chat_id or _get_default_chat_id()
    message = format_schedule_adjustment_alert(property_name, property_id, instruction)
    return send_telegram_message(target_chat_id, message)


def send_timeline_published_alert(
    property_name: str,
    property_id: str,
    tasks: list[dict],
    chat_id: str | None = None,
) -> bool:
    target_chat_id = chat_id or _get_default_chat_id()
    message = format_timeline_published_alert(property_name, property_id, tasks)
    return send_telegram_message(target_chat_id, message)


def format_draw_release_alert(
    property_name: str, milestone_name: str, draw_amount: float
) -> str:
    return (
        f"✨ <b>Milestone Draw Approved</b>\n\n"
        f"🎩 <b>Good day, sir.</b> Jeeves here with an official receipt from "
        f"<b>{html.escape(property_name)}</b>.\n\n"
        f"💷 <b>${draw_amount:,.2f}</b> has been authorized for the release "
        f"tied to: <b>{html.escape(milestone_name)}</b>.\n\n"
        f"I have logged the release in the ledger accordingly."
    )


def send_draw_release_alert(
    property_name: str,
    milestone_name: str,
    draw_amount: float,
    chat_id: str | None = None,
) -> bool:
    target_chat_id = chat_id or _get_default_chat_id()
    message = format_draw_release_alert(property_name, milestone_name, draw_amount)
    return send_telegram_message(target_chat_id, message)


def get_file_url(file_id: str) -> str | None:
    """Resolve a Telegram file_id (e.g. from a photo) to a downloadable
    URL. Returns None on any failure."""
    token = _get_bot_token()
    if not token:
        return None
    try:
        response = requests.get(
            f"{TELEGRAM_API_BASE}/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        if not response.ok:
            return None
        file_path = response.json().get("result", {}).get("file_path")
        if not file_path:
            return None
        return f"{TELEGRAM_API_BASE}/file/bot{token}/{file_path}"
    except requests.RequestException:
        return None


def download_file_bytes(file_id: str) -> bytes | None:
    """Download the actual bytes of a Telegram file (e.g. a photo), for
    re-hosting elsewhere (Supabase Storage) rather than depending on
    Telegram's own temporary file URLs. Returns None on any failure."""
    url = get_file_url(file_id)
    if not url:
        return None
    try:
        response = requests.get(url, timeout=20)
        if not response.ok:
            return None
        return response.content
    except requests.RequestException:
        return None


def extract_chat_messages(updates: list[dict], chat_id: str) -> list[dict]:
    """Shapes already-fetched raw Telegram updates belonging to `chat_id`
    into journal_entries-ready dicts (property_id/linked_line_item_id are
    added by the caller, which knows which property this chat belongs
    to). Pure — no network calls, no offset handling; see
    services/journal_sync.py for the fetch-once-and-distribute caller
    that gets `updates` from get_updates() and advances the offset
    (necessary since Telegram's offset is bot-wide, not per-chat — this
    function has to work from an already-fetched shared batch rather than
    calling get_updates() itself, or syncing one chat could silently
    consume updates another chat hasn't processed yet). Each entry carries
    enough identity (telegram_chat_id + telegram_message_id) to upsert
    without duplicating across repeated syncs.
    """
    chat_id_str = str(chat_id)
    entries = []
    for update in updates:
        message = update.get("message") or update.get("channel_post")
        if not message:
            continue
        if str(message.get("chat", {}).get("id")) != chat_id_str:
            continue

        from_user = message.get("from") or {}
        author_name = " ".join(
            part
            for part in [from_user.get("first_name"), from_user.get("last_name")]
            if part
        ) or from_user.get("username") or "Unknown"

        photo_sizes = message.get("photo") or []
        photo_file_id = photo_sizes[-1]["file_id"] if photo_sizes else None

        posted_at = datetime.fromtimestamp(
            message.get("date", 0), tz=timezone.utc
        ).isoformat()

        entries.append(
            {
                "telegram_message_id": message.get("message_id"),
                "telegram_chat_id": chat_id_str,
                "author_name": author_name,
                "message_text": message.get("text") or message.get("caption"),
                "photo_file_id": photo_file_id,
                "posted_at": posted_at,
            }
        )
    return entries


def send_status_update_alert(
    property_name: str,
    unit_name: str,
    task_name: str,
    old_status: str,
    new_status: str,
    cost: float,
    chat_id: str | None = None,
) -> bool:
    """Format and send a status-change alert to Telegram.

    chat_id should be the unit's own telegram_chat_id when available; falls
    back to the global TELEGRAM_CHAT_ID env var / secret otherwise. Silently
    no-ops (returns False) if neither the bot token nor a chat_id is
    configured.
    """
    target_chat_id = chat_id or _get_default_chat_id()
    message = format_status_alert(
        property_name, unit_name, task_name, old_status, new_status, cost
    )
    return send_telegram_message(target_chat_id, message)
