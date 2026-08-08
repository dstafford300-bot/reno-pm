-- Idempotency guard for webhook_main.py: Telegram retries a webhook
-- delivery if it doesn't get a fast-enough 200 response, which would
-- otherwise double-process a message (double Airtable record, double
-- Telegram reply, etc). Inserting update_id here (primary key) before
-- processing means a retry's insert just fails harmlessly and is skipped.
CREATE TABLE processed_telegram_updates (
    update_id BIGINT PRIMARY KEY,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
