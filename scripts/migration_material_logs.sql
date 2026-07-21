-- property-level link (unit_id stays as an optional finer-grained
-- assignment for later; material purchases are usually property-scoped
-- at time of receipt, not yet tied to one specific unit).
ALTER TABLE material_logs ADD COLUMN property_id UUID REFERENCES properties(id) ON DELETE CASCADE;

-- Permanent Supabase Storage URL for a receipt photo (downloaded from
-- Telegram and re-hosted, rather than depending on Telegram's own
-- temporary file retention).
ALTER TABLE material_logs ADD COLUMN photo_url TEXT;

-- 'telegram' | 'manual' (pasted digital receipt) | 'manual-photo', etc.
ALTER TABLE material_logs ADD COLUMN source VARCHAR(50) DEFAULT 'manual';

-- Itemized line items from a parsed receipt, e.g.
-- [{"description": "2x4x8 Lumber", "cost": 45.00}, ...] — descriptive
-- detail only, not linked to the SOW line_items table.
ALTER TABLE material_logs ADD COLUMN line_items_json JSONB;
