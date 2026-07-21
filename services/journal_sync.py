from supabase import Client

from services.journal_ai import filter_relevant_messages
from services.receipt_ingest import is_receipt_message, process_receipt_message
from services.telegram_bot import extract_chat_messages, get_updates

_OFFSET_KEY = "telegram_update_offset"


def _get_stored_offset(client: Client) -> int | None:
    rows = (
        client.table("bot_state")
        .select("value")
        .eq("key", _OFFSET_KEY)
        .execute()
        .data
    )
    return int(rows[0]["value"]) if rows else None


def _store_offset(client: Client, offset: int) -> None:
    client.table("bot_state").upsert(
        {"key": _OFFSET_KEY, "value": str(offset)}
    ).execute()


def _sync_one_property(
    client: Client, property_id: str, chat_id: str, messages: list[dict]
) -> dict:
    """Dedupes/filters/logs one property's slice of an already-fetched
    update batch — see sync_all_journals for why the fetch itself happens
    once, upstream, rather than per property."""
    already_synced = (
        client.table("journal_entries")
        .select("telegram_message_id")
        .eq("telegram_chat_id", str(chat_id))
        .execute()
        .data
    )
    already_synced_ids = {row["telegram_message_id"] for row in already_synced}

    new_messages = [
        m for m in messages if m["telegram_message_id"] not in already_synced_ids
    ]
    if not new_messages:
        return {"seen": len(messages), "new": 0, "kept": 0, "receipts": 0}

    receipt_messages = [m for m in new_messages if is_receipt_message(m)]
    regular_messages = [m for m in new_messages if not is_receipt_message(m)]

    receipts_logged = 0
    for m in receipt_messages:
        if process_receipt_message(client, property_id, m):
            receipts_logged += 1

    relevant = filter_relevant_messages(regular_messages) if regular_messages else []
    # Receipts always land in the journal too, regardless of the AI's
    # chatter-filtering judgment — the keyword is an explicit signal.
    to_journal = relevant + receipt_messages
    for m in to_journal:
        m["property_id"] = property_id

    if to_journal:
        client.table("journal_entries").upsert(
            to_journal, on_conflict="telegram_chat_id,telegram_message_id"
        ).execute()

    return {
        "seen": len(messages),
        "new": len(new_messages),
        "kept": len(to_journal),
        "receipts": receipts_logged,
    }


def sync_all_journals(client: Client, properties: list[dict]) -> dict[str, dict]:
    """Single shared entry point for pulling Telegram updates and syncing
    every linked property's journal in one pass.

    Telegram's update offset is bot-wide, not per-chat, so fetching once
    here and distributing per chat_id is required — syncing one property
    at a time, each with its own independent get_updates() call, was the
    actual cause of the old "gaps if syncs are infrequent" limitation:
    without ever advancing the offset at all, a busy bot gets stuck
    re-seeing the same oldest ~100 updates forever (Telegram's per-call
    cap) and never reaches anything newer, across every chat it's in —
    not just delayed, genuinely unreachable once traffic exceeds that
    window. Advancing a *shared* offset once per batch, after every known
    property has had a chance to claim its slice, fixes that without one
    property's sync accidentally consuming another's unprocessed updates.

    Returns {property_id: {"seen","new","kept","receipts"}} for every
    property with a linked telegram_chat_id.
    """
    offset = None
    try:
        offset = _get_stored_offset(client)
    except Exception:
        pass  # bot_state not migrated yet — degrades to the old
        # replay-the-same-window behavior rather than erroring.

    raw_updates = get_updates(offset=offset)

    max_update_id = None
    for update in raw_updates:
        update_id = update.get("update_id")
        if update_id is not None:
            max_update_id = (
                update_id if max_update_id is None else max(max_update_id, update_id)
            )

    results = {}
    for prop in properties:
        chat_id = prop.get("telegram_chat_id")
        if not chat_id:
            continue
        messages = extract_chat_messages(raw_updates, chat_id)
        results[prop["id"]] = _sync_one_property(
            client, prop["id"], str(chat_id), messages
        )

    if max_update_id is not None:
        try:
            _store_offset(client, max_update_id + 1)
        except Exception:
            pass

    return results


def sync_property_journal(client: Client, property_id: str, chat_id: str) -> dict:
    """Single-property entry point (the Journal page's manual sync
    button) — routed through sync_all_journals so the shared offset
    still advances correctly regardless of which property triggered it,
    rather than each manual click risking the same cross-property
    stomping sync_all_journals exists to avoid."""
    properties = (
        client.table("properties").select("id, telegram_chat_id").execute().data
    )
    results = sync_all_journals(client, properties)
    return results.get(property_id, {"seen": 0, "new": 0, "kept": 0, "receipts": 0})
