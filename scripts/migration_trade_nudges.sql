-- One-shot flags so each look-ahead tier fires exactly once per task,
-- not once per nightly run. See services/trade_nudges.py.
ALTER TABLE line_items ADD COLUMN nudge_48h_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE line_items ADD COLUMN nudge_24h_sent BOOLEAN NOT NULL DEFAULT FALSE;
