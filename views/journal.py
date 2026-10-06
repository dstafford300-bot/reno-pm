import html
from datetime import datetime

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import link_journal_entry_to_line_item
from services.telegram_bot import get_file_url


def render():
    st.title("📓 Project Journal")

    supabase = get_supabase_client()
    try:
        properties = (
            supabase.table("properties")
            .select("id, property_name, telegram_chat_id, archived")
            .order("property_name")
            .execute()
            .data
        )
    except Exception:
        properties = (
            supabase.table("properties")
            .select("id, property_name")
            .order("property_name")
            .execute()
            .data
        )
        for p in properties:
            p["telegram_chat_id"] = None
            p["archived"] = False

    if not properties:
        st.info("No properties yet. Upload a SOW to get started.")
        return

    selected_name = st.selectbox("Property", [p["property_name"] for p in properties])
    selected_property = next(
        p for p in properties if p["property_name"] == selected_name
    )
    property_id = selected_property["id"]
    chat_id = selected_property.get("telegram_chat_id")
    is_archived = bool(selected_property.get("archived"))

    if is_archived:
        st.info(
            "🔒 This project is finished and read-only — syncing and "
            "task-linking are disabled. Reopen it from the Dashboard to "
            "make changes again."
        )

    if not chat_id:
        st.info(
            "This property has no linked Telegram group yet — link one on "
            "the Dashboard's 🔗 Telegram Group Sync section first."
        )
        return

    st.caption(
        "✅ Journal entries and receipts sync automatically in real time "
        "now — no manual sync needed."
    )

    units = (
        supabase.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .execute()
        .data
    )
    unit_ids = [u["id"] for u in units]
    line_items = []
    if unit_ids:
        line_items = (
            supabase.table("line_items")
            .select("id, task_name")
            .in_("unit_id", unit_ids)
            .execute()
            .data
        )
    line_item_name_by_id = {item["id"]: item["task_name"] for item in line_items}

    try:
        entries = (
            supabase.table("journal_entries")
            .select(
                "id, author_name, message_text, photo_file_id, posted_at, "
                "linked_line_item_id"
            )
            .eq("property_id", property_id)
            .order("posted_at", desc=True)
            .execute()
            .data
        )
    except Exception:
        st.error(
            "The `journal_entries` table doesn't exist yet in the database "
            "— run the migration in scripts/migration_draw_and_journal.sql "
            "via Supabase's SQL Editor, then refresh."
        )
        return

    if not entries:
        st.info(
            "No journal entries yet. Click 🔄 Sync Journal from Telegram "
            "above, after some activity has happened in the group."
        )
        return

    # A burst of photos from one person (Telegram sends an album as separate
    # messages) is one visit's worth of pictures — show it as one gallery
    # card instead of dozens of near-identical entries.
    groups: list[list[dict]] = []
    for entry in entries:
        if (
            groups
            and entry.get("photo_file_id")
            and not entry.get("message_text")
            and groups[-1][0].get("photo_file_id")
            and not groups[-1][0].get("message_text")
            and groups[-1][0]["author_name"] == entry["author_name"]
            and abs(
                (
                    datetime.fromisoformat(groups[-1][-1]["posted_at"])
                    - datetime.fromisoformat(entry["posted_at"])
                ).total_seconds()
            )
            <= 600
        ):
            groups[-1].append(entry)
        else:
            groups.append([entry])

    for group in groups:
        entry = group[0]
        posted = datetime.fromisoformat(entry["posted_at"])
        with st.container(border=True):
            st.caption(f"{posted.strftime('%b %-d, %Y — %I:%M %p')} · {entry['author_name']}")
            if entry.get("message_text"):
                st.markdown(html.escape(entry["message_text"]))
            photo_urls = [
                url
                for url in (
                    get_file_url(g["photo_file_id"])
                    for g in group
                    if g.get("photo_file_id")
                )
                if url
            ]
            if len(group) > 1:
                st.caption(f"📷 {len(group)} photos")
            if photo_urls:
                for row_start in range(0, len(photo_urls), 3):
                    cols = st.columns(3)
                    for col, url in zip(cols, photo_urls[row_start : row_start + 3]):
                        col.image(url)
            elif any(g.get("photo_file_id") for g in group):
                st.caption("📷 Photo attached (couldn't be loaded)")

            linked_name = line_item_name_by_id.get(entry.get("linked_line_item_id"))
            link_options = ["(none)"] + [item["task_name"] for item in line_items]
            default_index = (
                link_options.index(linked_name) if linked_name in link_options else 0
            )
            choice = st.selectbox(
                "Linked task",
                link_options,
                index=default_index,
                key=f"journal_link_{entry['id']}",
                label_visibility="collapsed",
                disabled=is_archived,
            )
            if choice != (linked_name or "(none)") and not is_archived:
                new_id = None
                if choice != "(none)":
                    new_id = next(
                        item["id"] for item in line_items if item["task_name"] == choice
                    )
                for g in group:
                    link_journal_entry_to_line_item(supabase, g["id"], new_id)
                st.rerun()
