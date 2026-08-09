-- Idempotency guard for webhook_main.py: Telegram retries a webhook
-- delivery if it doesn't get a fast-enough 200 response, which would
-- otherwise double-process a message (double Airtable record, double
-- Telegram reply, etc). Inserting (bot, update_id) here before processing
-- means a retry's insert just fails harmlessly and is skipped.
--
-- Keyed by bot as well as update_id because Telegram's update_id counter
-- is per-bot, not global — Jeeves and Matty are two separate bots, so
-- their update_id sequences can legitimately overlap.
CREATE TABLE processed_telegram_updates (
    bot VARCHAR(20) NOT NULL,
    update_id BIGINT NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW()),
    PRIMARY KEY (bot, update_id)
);
