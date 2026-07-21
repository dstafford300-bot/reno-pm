-- Tiny key-value store for bot-wide state that needs to persist between
-- runs — currently just the Telegram getUpdates offset, which fixes a
-- real bug: without a persisted, advancing offset, Telegram keeps
-- re-serving the same oldest ~100 unconfirmed updates bot-wide forever,
-- so a busy bot could get stuck and never see newer messages. See
-- services/journal_sync.py's sync_all_journals().
CREATE TABLE bot_state (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
