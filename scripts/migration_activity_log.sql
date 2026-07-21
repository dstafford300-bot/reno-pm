-- Durable log of "things that happened" per property, written alongside
-- the existing per-property Telegram alerts — needed because the PM's
-- cross-property daily digest (services/pm_digest.py) has to summarize
-- "what changed yesterday" across every property, and there was
-- previously no persisted record of that; line_items/draw_milestones
-- only hold current state, not a history of changes.
CREATE TABLE activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    category VARCHAR(50) NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
