-- Holds one pending bulk status-update proposal per DM chat, so a
-- confirmation ("yes") sent as a separate Telegram message can find and
-- apply what was previously proposed. See services/bulk_status_update.py.
-- One row per chat_id — a new proposal simply overwrites any unconfirmed
-- previous one.
CREATE TABLE pending_bulk_updates (
    chat_id VARCHAR(50) PRIMARY KEY,
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    property_name VARCHAR(255) NOT NULL,
    line_item_ids UUID[] NOT NULL,
    new_status VARCHAR(50) NOT NULL,
    new_percent NUMERIC(5, 2) NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
