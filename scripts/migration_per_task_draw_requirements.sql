-- Actual, real progress per task (separate from the 3-state status enum),
-- tracked from the Schedule page.
ALTER TABLE line_items ADD COLUMN percent_complete NUMERIC(5, 2) DEFAULT 0;

-- Replaces draw_milestones.linked_line_item_ids: each linked task now
-- carries its OWN required completion threshold, not a shared one.
CREATE TABLE draw_milestone_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    milestone_id UUID REFERENCES draw_milestones(id) ON DELETE CASCADE,
    line_item_id UUID REFERENCES line_items(id) ON DELETE CASCADE,
    required_percent NUMERIC(5, 2) NOT NULL DEFAULT 100,
    UNIQUE (milestone_id, line_item_id)
);

-- Superseded by draw_milestone_tasks and per-task actual progress.
ALTER TABLE draw_milestones DROP COLUMN linked_line_item_ids;
ALTER TABLE draw_milestones DROP COLUMN percent_complete;
