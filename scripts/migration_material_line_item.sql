-- Line-item-level cost tracking: lets a material purchase be matched to
-- the specific scope-of-work task it was bought for (not just the
-- property), so actual spend can be compared against that task's
-- budgeted_cost instead of only tracking property-wide totals.
ALTER TABLE material_logs ADD COLUMN line_item_id UUID REFERENCES line_items(id) ON DELETE SET NULL;
