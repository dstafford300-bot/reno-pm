-- Dedupe for services/email_receipts.py: which receipt emails (by their
-- Message-ID header) have already been turned into material_logs rows.
-- Replaces relying on the mailbox's read/unread flag, which a Gmail
-- filter that auto-marks receipts read would defeat.
CREATE TABLE processed_emails (
    message_id TEXT PRIMARY KEY,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
