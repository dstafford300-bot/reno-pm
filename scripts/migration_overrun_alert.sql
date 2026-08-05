-- One-shot flag so a task's budget-overrun Telegram alert fires exactly
-- once, not on every subsequent purchase logged against an already-over
-- task. See services/db_writer.py's check_line_item_overrun().
ALTER TABLE line_items ADD COLUMN overrun_alerted BOOLEAN NOT NULL DEFAULT FALSE;
