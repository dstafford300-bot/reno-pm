-- Jeeves bulk updates now handle a whole reply in one go: many tasks at
-- different statuses/percentages across properties, plus draws marked paid.
-- The proposal is stored as one JSON document instead of one status + id list.
ALTER TABLE pending_bulk_updates ADD COLUMN changes JSONB;
ALTER TABLE pending_bulk_updates ALTER COLUMN property_name DROP NOT NULL;
ALTER TABLE pending_bulk_updates ALTER COLUMN line_item_ids DROP NOT NULL;
ALTER TABLE pending_bulk_updates ALTER COLUMN new_status DROP NOT NULL;
ALTER TABLE pending_bulk_updates ALTER COLUMN new_percent DROP NOT NULL;
