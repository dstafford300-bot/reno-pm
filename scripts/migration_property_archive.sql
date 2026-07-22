-- "Mark Project Finished" support — an archived property becomes
-- read-only across every page (Schedule/Budget/Journal/Dashboard edits
-- all check this flag) while staying fully viewable, and can be
-- reopened at any time. See views/dashboard.py's "📁 Project Status"
-- section.
ALTER TABLE properties ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE;
