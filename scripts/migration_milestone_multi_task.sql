ALTER TABLE draw_milestones DROP COLUMN linked_line_item_id;
ALTER TABLE draw_milestones ADD COLUMN linked_line_item_ids UUID[] DEFAULT '{}';
