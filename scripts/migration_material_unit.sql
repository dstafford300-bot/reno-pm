-- Materials are filed by UNIT (read from the job name a contractor writes
-- on the receipt, e.g. "809 Fred Unit 3 kitchen"), not by individual task:
-- receipts almost always span several tasks, and the PM thinks of
-- materials per unit. line_item_id stays for older rows.
ALTER TABLE material_logs ADD COLUMN unit_id UUID REFERENCES units(id) ON DELETE SET NULL;

-- True when the receipt named no unit and the purchase was filed under the
-- property's default unit instead — kept visible so it can be corrected.
ALTER TABLE material_logs ADD COLUMN unit_is_assumed BOOLEAN NOT NULL DEFAULT FALSE;

-- The unit currently being worked in: the fallback for receipts that
-- don't name one.
ALTER TABLE properties ADD COLUMN default_material_unit_id UUID REFERENCES units(id) ON DELETE SET NULL;
