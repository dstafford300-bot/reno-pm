-- Materials are filed by UNIT (read from the job name a contractor writes
-- on the receipt, e.g. "809 Fred Unit 3 kitchen"), not by individual task:
-- receipts almost always span several tasks, and the PM thinks of
-- materials per unit. line_item_id stays for older rows.
ALTER TABLE material_logs ADD COLUMN unit_id UUID REFERENCES units(id) ON DELETE SET NULL;
