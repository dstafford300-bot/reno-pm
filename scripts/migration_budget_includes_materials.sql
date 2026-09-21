-- A task's budgeted_cost is LABOR only for most SOW items — materials are
-- bought separately and tracked as materials (Material Logs), not against
-- that budget. A few tasks (e.g. a roof) are quoted as labor AND
-- materials; only those have a budget that material spend should be
-- compared to. This flag marks them. Variance tracking and the 90%-of-
-- budget overrun alerts apply only where it's TRUE.
ALTER TABLE line_items ADD COLUMN budget_includes_materials BOOLEAN NOT NULL DEFAULT FALSE;
