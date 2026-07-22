import html
from datetime import datetime

import streamlit as st

from db.connection import get_supabase_client
from services.db_writer import link_journal_entry_to_line_item
from services.journal_sync import sync_property_journal
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

    if st.button("🔄 Sync Journal from Telegram", disabled=is_archived):
        with st.spinner("Fetching messages and asking Jeeves to filter..."):
            try:
                result = sync_property_journal(supabase, property_id, chat_id)
            except Exception as e:
                st.error(
                    "Sync failed — make sure the required migrations have "
                    "been run (scripts/migration_draw_and_journal.sql and "
                    "scripts/migration_material_logs.sql via Supabase's SQL "
                    f"Editor). Error: {e}"
                )
                return
        st.success(
            f"Saw {result['seen']} message(s), kept {result['kept']} in the "
            f"journal, logged {result['receipts']} receipt(s)."
        )
        st.rerun()

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

    for entry in entries:
        posted = datetime.fromisoformat(entry["posted_at"])
        with st.container(border=True):
            st.caption(f"{posted.strftime('%b %-d, %Y — %I:%M %p')} · {entry['author_name']}")
            if entry.get("message_text"):
                st.markdown(html.escape(entry["message_text"]))
            if entry.get("photo_file_id"):
                photo_url = get_file_url(entry["photo_file_id"])
                if photo_url:
                    st.image(photo_url)
                else:
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
                link_journal_entry_to_line_item(supabase, entry["id"], new_id)
                st.rerun()
