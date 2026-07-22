from datetime import datetime, timezone

from supabase import Client


def log_activity(
    supabase: Client, property_id: str, category: str, summary: str
) -> None:
    """Records one line of durable history for the PM's cross-property
    daily digest (services/pm_digest.py) — call this alongside sending a
    per-property Telegram alert, not instead of it. Never raises: a
    logging failure (e.g. migration not run yet) shouldn't block the
    actual alert it's paired with."""
    try:
        supabase.table("activity_log").insert(
            {"property_id": property_id, "category": category, "summary": summary}
        ).execute()
    except Exception:
        pass


def create_material_log(
    supabase: Client,
    store: str,
    amount: float,
    property_id: str | None = None,
    purchase_date: str | None = None,
    receipt_details: str | None = None,
    photo_url: str | None = None,
    source: str = "manual",
    line_items_json: list[dict] | None = None,
) -> dict:
    payload = {
        "store": store,
        "amount": amount,
        "property_id": property_id,
        "receipt_details": receipt_details,
        "photo_url": photo_url,
        "source": source,
        "line_items_json": line_items_json,
    }
    if purchase_date:
        payload["purchase_date"] = purchase_date
    return supabase.table("material_logs").insert(payload).execute().data[0]


def assign_material_log_property(
    supabase: Client, material_log_id: str, property_id: str
) -> dict:
    return (
        supabase.table("material_logs")
        .update({"property_id": property_id})
        .eq("id", material_log_id)
        .execute()
        .data[0]
    )


def create_draw_milestone(
    supabase: Client,
    property_id: str,
    milestone_name: str,
    draw_amount: float,
    task_requirements: list[dict] | None = None,
) -> dict:
    """task_requirements: [{"line_item_id": ..., "required_percent": ...}, ...]
    — each linked task carries its own completion threshold that must be
    met (via its real percent_complete, tracked on the Schedule page)
    before the milestone is eligible for release."""
    milestone = (
        supabase.table("draw_milestones")
        .insert(
            {
                "property_id": property_id,
                "milestone_name": milestone_name,
                "draw_amount": draw_amount,
            }
        )
        .execute()
        .data[0]
    )

    if task_requirements:
        rows = [
            {
                "milestone_id": milestone["id"],
                "line_item_id": req["line_item_id"],
                "required_percent": req["required_percent"],
            }
            for req in task_requirements
        ]
        supabase.table("draw_milestone_tasks").insert(rows).execute()

    return milestone


def get_milestone_task_progress(supabase: Client, milestone_id: str) -> list[dict]:
    """Returns each task linked to this milestone with its required_percent
    alongside the task's actual, real-world percent_complete and task_name."""
    requirements = (
        supabase.table("draw_milestone_tasks")
        .select("line_item_id, required_percent")
        .eq("milestone_id", milestone_id)
        .execute()
        .data
    )
    if not requirements:
        return []

    line_item_ids = [r["line_item_id"] for r in requirements]
    items = (
        supabase.table("line_items")
        .select("id, task_name, percent_complete")
        .in_("id", line_item_ids)
        .execute()
        .data
    )
    items_by_id = {i["id"]: i for i in items}

    result = []
    for req in requirements:
        item = items_by_id.get(req["line_item_id"])
        if not item:
            continue
        result.append(
            {
                "line_item_id": req["line_item_id"],
                "task_name": item["task_name"],
                "required_percent": float(req["required_percent"]),
                "actual_percent": float(item.get("percent_complete") or 0),
            }
        )
    return result


def delete_draw_milestone(supabase: Client, milestone_id: str) -> None:
    """Its draw_milestone_tasks rows cascade-delete automatically (FK
    ON DELETE CASCADE) — no separate cleanup needed."""
    supabase.table("draw_milestones").delete().eq("id", milestone_id).execute()


def milestone_is_eligible(task_progress: list[dict]) -> bool:
    """True once every linked task's actual percent_complete has reached
    its own required_percent. False (not eligible) if the milestone has no
    linked tasks at all — nothing to verify progress against."""
    if not task_progress:
        return False
    return all(t["actual_percent"] >= t["required_percent"] for t in task_progress)


def update_line_item_percent(
    supabase: Client, line_item_id: str, percent_complete: float
) -> dict:
    return (
        supabase.table("line_items")
        .update({"percent_complete": percent_complete})
        .eq("id", line_item_id)
        .execute()
        .data[0]
    )


def release_draw_milestone(supabase: Client, milestone_id: str) -> dict:
    return (
        supabase.table("draw_milestones")
        .update(
            {
                "status": "Released",
                "released_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", milestone_id)
        .execute()
        .data[0]
    )


def upsert_journal_entries(supabase: Client, entries: list[dict]) -> int:
    """Insert journal entries, skipping any that already exist for the same
    (telegram_chat_id, telegram_message_id) pair — relies on the table's
    UNIQUE constraint on that pair rather than checking existence in Python,
    so a re-sync is always safe to re-run."""
    if not entries:
        return 0
    result = (
        supabase.table("journal_entries")
        .upsert(entries, on_conflict="telegram_chat_id,telegram_message_id")
        .execute()
    )
    return len(result.data)


def link_journal_entry_to_line_item(
    supabase: Client, entry_id: str, line_item_id: str | None
) -> None:
    supabase.table("journal_entries").update(
        {"linked_line_item_id": line_item_id}
    ).eq("id", entry_id).execute()


def update_line_item_status(supabase: Client, line_item_id: str, status: str) -> None:
    supabase.table("line_items").update({"status": status}).eq(
        "id", line_item_id
    ).execute()


def set_property_telegram_chat_id(
    supabase: Client, property_id: str, chat_id: str | int
) -> None:
    supabase.table("properties").update({"telegram_chat_id": str(chat_id)}).eq(
        "id", property_id
    ).execute()


def set_property_archived(supabase: Client, property_id: str, archived: bool) -> None:
    """Archiving doesn't touch or hide any data — every page that allows
    edits (Dashboard/Schedule/Budget/Journal) checks this flag itself and
    shows a read-only banner instead of its normal controls."""
    supabase.table("properties").update({"archived": archived}).eq(
        "id", property_id
    ).execute()


def delete_property_cascade(supabase: Client, property_id: str) -> None:
    """Permanently deletes a property and everything under it.

    Deletes children explicitly in dependency order rather than relying
    on ON DELETE CASCADE — units/line_items predate this session and
    their FK behavior was never directly confirmed, so this is correct
    whether or not cascades are configured (deleting an already-cascaded
    row is just a harmless no-op).
    """
    units = (
        supabase.table("units").select("id").eq("property_id", property_id).execute().data
    )
    unit_ids = [u["id"] for u in units]

    line_item_ids = []
    if unit_ids:
        line_items = (
            supabase.table("line_items")
            .select("id")
            .in_("unit_id", unit_ids)
            .execute()
            .data
        )
        line_item_ids = [i["id"] for i in line_items]

    milestones = (
        supabase.table("draw_milestones")
        .select("id")
        .eq("property_id", property_id)
        .execute()
        .data
    )
    milestone_ids = [m["id"] for m in milestones]
    if milestone_ids:
        supabase.table("draw_milestone_tasks").delete().in_(
            "milestone_id", milestone_ids
        ).execute()
    supabase.table("draw_milestones").delete().eq("property_id", property_id).execute()

    supabase.table("journal_entries").delete().eq("property_id", property_id).execute()
    supabase.table("material_logs").delete().eq("property_id", property_id).execute()
    try:
        supabase.table("activity_log").delete().eq("property_id", property_id).execute()
    except Exception:
        pass  # migration_activity_log.sql not run yet — nothing to clean up

    if line_item_ids:
        supabase.table("line_items").delete().in_("id", line_item_ids).execute()
    if unit_ids:
        supabase.table("units").delete().in_("id", unit_ids).execute()

    supabase.table("properties").delete().eq("id", property_id).execute()


def clear_property_telegram_chat_id(supabase: Client, property_id: str) -> None:
    supabase.table("properties").update({"telegram_chat_id": None}).eq(
        "id", property_id
    ).execute()


def apply_schedule_adjustments(
    supabase: Client, property_id: str, updated_tasks: list[dict]
) -> dict:
    """Reconcile updated_tasks (from refine_timeline_draft, keyed by the
    same 'Unit Name: Task Name' labels produced by active_tasks_from_line_
    items) against this property's existing line_items:

      - a task with renamed_from set to an existing label -> UPDATE that
        SAME row's task_name (and dates/cost_group/dependencies below) —
        preserves its id, so its tracked status survives the rename.
      - a task whose label matches an existing row -> UPDATE its dates,
        cost_group, and resolved dependencies.
      - a task whose label matches nothing existing and has no usable
        renamed_from (e.g. one half of a split) -> INSERT it as a new
        line_item in the unit named by its label prefix.
      - an existing row whose label is no longer present in updated_tasks
        (e.g. the original pre-split task) -> DELETE it, since it's been
        superseded.

    Matching is unit-prefixed (not plain task_name) because the same
    task_name can legitimately appear in two different units of the same
    property — plain-name matching would silently collide and update the
    wrong row (confirmed against real data: "New kitchen — cabinets,
    countertop, sink" exists in two separate units on one property).

    Returns {"updated": n, "inserted": n, "deleted": n, "renamed": n}.
    """
    units = (
        supabase.table("units")
        .select("id, unit_name")
        .eq("property_id", property_id)
        .execute()
        .data
    )
    unit_ids = [u["id"] for u in units]
    if not unit_ids:
        return {"updated": 0, "inserted": 0, "deleted": 0, "renamed": 0}
    unit_id_to_name = {u["id"]: u["unit_name"] for u in units}
    unit_name_to_id = {u["unit_name"]: u["id"] for u in units}

    existing = (
        supabase.table("line_items")
        .select("id, unit_id, task_name")
        .in_("unit_id", unit_ids)
        .execute()
        .data
    )

    def _label(row: dict) -> str:
        unit_name = unit_id_to_name.get(row["unit_id"])
        return f"{unit_name}: {row['task_name']}" if unit_name else row["task_name"]

    def _split_label(label: str) -> tuple[str | None, str]:
        """('Unit Name', 'Task Name') if label has a recognized unit
        prefix, else (None, label) unchanged."""
        if ": " in label:
            prefix, rest = label.split(": ", 1)
            if prefix in unit_name_to_id:
                return prefix, rest
        return None, label

    label_to_id = {_label(row): row["id"] for row in existing}
    updated_labels = {task["task_name"] for task in updated_tasks}

    # Renames: claim an existing row's id for the new label (via a
    # task_name UPDATE) instead of delete+insert, so its status survives.
    renamed_count = 0
    for task in updated_tasks:
        old_label = task.get("renamed_from")
        new_label = task["task_name"]
        if not old_label or old_label == new_label:
            continue
        old_item_id = label_to_id.get(old_label)
        if not old_item_id:
            continue  # AI referenced a task_name we don't recognize; ignore
        _, bare_new_name = _split_label(new_label)
        supabase.table("line_items").update({"task_name": bare_new_name}).eq(
            "id", old_item_id
        ).execute()
        del label_to_id[old_label]
        label_to_id[new_label] = old_item_id
        renamed_count += 1

    # Delete rows superseded by a split/merge (their label no longer
    # appears anywhere in the revised task list, and wasn't claimed by a
    # rename above).
    deleted_count = 0
    for label, item_id in list(label_to_id.items()):
        if label not in updated_labels:
            supabase.table("line_items").delete().eq("id", item_id).execute()
            del label_to_id[label]
            deleted_count += 1

    # Insert tasks with no existing match, each into the unit named by its
    # label prefix. Tasks we can't confidently place (no recognized unit
    # prefix) are skipped rather than guessed at.
    inserted_count = 0
    for task in updated_tasks:
        label = task["task_name"]
        if label in label_to_id:
            continue
        unit_name, bare_task_name = _split_label(label)
        unit_id = unit_name_to_id.get(unit_name) if unit_name else None
        if not unit_id:
            continue
        row = (
            supabase.table("line_items")
            .insert(
                {
                    "unit_id": unit_id,
                    "task_name": bare_task_name,
                    "cost_group": task.get("cost_group"),
                    "budgeted_cost": 0,
                    "start_date": task.get("start_date"),
                    "estimated_end_date": task.get("estimated_end_date"),
                }
            )
            .execute()
            .data[0]
        )
        label_to_id[label] = row["id"]
        inserted_count += 1

    # Now update dates/cost_group/dependencies for every task — both the
    # pre-existing matches and the ones just inserted above.
    updated_count = 0
    for task in updated_tasks:
        item_id = label_to_id.get(task["task_name"])
        if not item_id:
            continue
        dependency_ids = [
            label_to_id[name]
            for name in (task.get("depends_on") or [])
            if name in label_to_id
        ]
        supabase.table("line_items").update(
            {
                "start_date": task.get("start_date"),
                "estimated_end_date": task.get("estimated_end_date"),
                "cost_group": task.get("cost_group"),
                "dependencies": dependency_ids,
            }
        ).eq("id", item_id).execute()
        updated_count += 1

    return {
        "updated": updated_count,
        "inserted": inserted_count,
        "deleted": deleted_count,
        "renamed": renamed_count,
    }


def publish_draft_timeline(
    supabase: Client, property_id: str, unit_name: str, tasks: list[dict]
) -> dict:
    """Insert a flat drafted task list (from the Timeline Drafting Sandbox)
    into a single new unit under property_id.

    depends_on in each task references another task's task_name, which
    doesn't have a real database ID until after insertion — so this runs a
    second pass: insert everything first, then resolve each task's
    depends_on names to the real line_item UUIDs and write them into the
    dependencies column. Resolution is done by task_name lookup (not
    positional/insert-order matching), since row order in a bulk INSERT's
    returned data isn't a guarantee worth depending on.
    """
    unit_row = (
        supabase.table("units")
        .insert({"property_id": property_id, "unit_name": unit_name})
        .execute()
        .data[0]
    )

    rows = [
        {
            "unit_id": unit_row["id"],
            "task_name": task["task_name"],
            "cost_group": task.get("cost_group"),
            "budgeted_cost": task.get("budgeted_cost", 0),
            "start_date": task.get("start_date"),
            "estimated_end_date": task.get("estimated_end_date"),
        }
        for task in tasks
    ]
    inserted = supabase.table("line_items").insert(rows).execute().data

    task_name_to_id = {row["task_name"]: row["id"] for row in inserted}

    for task in tasks:
        depends_on_names = task.get("depends_on") or []
        dependency_ids = [
            task_name_to_id[name]
            for name in depends_on_names
            if name in task_name_to_id
        ]
        if dependency_ids:
            item_id = task_name_to_id.get(task["task_name"])
            if item_id:
                supabase.table("line_items").update(
                    {"dependencies": dependency_ids}
                ).eq("id", item_id).execute()

    return {"unit_id": unit_row["id"], "line_items": len(inserted)}


def save_parsed_sow(supabase: Client, parsed: dict) -> dict:
    """Insert a parsed {properties: [{units: [{line_items: [...]}]}]} payload
    into the properties / units / line_items tables, wiring up the relational
    IDs returned by each insert."""
    summary = {"properties": 0, "units": 0, "line_items": 0}

    for prop in parsed.get("properties", []):
        property_row = (
            supabase.table("properties")
            .insert(
                {
                    "property_name": prop["property_name"],
                    "address": prop.get("address"),
                }
            )
            .execute()
            .data[0]
        )
        summary["properties"] += 1

        for unit in prop.get("units", []):
            unit_row = (
                supabase.table("units")
                .insert(
                    {
                        "property_id": property_row["id"],
                        "unit_name": unit["unit_name"],
                    }
                )
                .execute()
                .data[0]
            )
            summary["units"] += 1

            line_items = unit.get("line_items", [])
            if not line_items:
                continue

            rows = [
                {
                    "unit_id": unit_row["id"],
                    "task_name": item["task_name"],
                    "cost_group": item.get("cost_group"),
                    "budgeted_cost": item.get("budgeted_cost", 0),
                    "start_date": item.get("start_date"),
                    "estimated_end_date": item.get("estimated_end_date"),
                    "notes": item.get("notes"),
                }
                for item in line_items
            ]
            supabase.table("line_items").insert(rows).execute()
            summary["line_items"] += len(rows)

    return summary
