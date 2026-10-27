-- Every incoming Telegram update, stored the moment it arrives, with whether
-- it was actually handled. Replaces processed_telegram_updates, which marked
-- an update "done" BEFORE handling it — so a failure lost the message with no
-- trace and no retry.
--   status: received (waiting/in progress) -> handled | failed (will retry)
--           -> dead (gave up after several tries; owner is alerted)
CREATE TABLE telegram_inbox (
    id BIGSERIAL PRIMARY KEY,
    bot VARCHAR(20) NOT NULL,
    update_id BIGINT NOT NULL,
    chat_id VARCHAR(50),
    chat_type VARCHAR(20),
    user_id VARCHAR(50),
    message_id BIGINT,
    kind VARCHAR(30) NOT NULL,   -- text photo voice video video_note audio document edited other
    text TEXT,
    payload JSONB NOT NULL,      -- the complete raw update
    status VARCHAR(20) NOT NULL DEFAULT 'received',
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW()),
    handled_at TIMESTAMP WITH TIME ZONE,
    UNIQUE (bot, update_id)
);
CREATE INDEX telegram_inbox_pending_idx ON telegram_inbox (status, received_at);
CREATE INDEX telegram_inbox_message_idx ON telegram_inbox (chat_id, message_id);

-- Journal entries can now hold a photo/voice note/video copied into our own
-- private storage (Telegram file ids alone stop working if Telegram drops
-- the file), and remember when a message was edited.
ALTER TABLE journal_entries ADD COLUMN media_kind VARCHAR(20);
ALTER TABLE journal_entries ADD COLUMN media_path TEXT;
ALTER TABLE journal_entries ADD COLUMN edited_at TIMESTAMP WITH TIME ZONE;
