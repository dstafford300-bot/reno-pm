-- Accounts and permissions for the new installable app (see docs/APP_PLAN.md).
--
-- Roles:  owner (everything) | pm (sees all, edits schedule, budgets
-- read-only) | contractor (sees assigned properties' schedule, can only
-- PROPOSE changes — applied when the owner approves).

CREATE TABLE app_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('owner', 'pm', 'contractor')),
    password_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);

-- Which properties a contractor can see (owner and pm see all).
CREATE TABLE user_properties (
    user_id UUID REFERENCES app_users(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, property_id)
);

-- A contractor's proposed task update, held until the owner approves it.
CREATE TABLE change_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    line_item_id UUID REFERENCES line_items(id) ON DELETE CASCADE,
    requested_by UUID REFERENCES app_users(id) ON DELETE SET NULL,
    task_label TEXT NOT NULL,
    changes JSONB NOT NULL,          -- {"status":..,"percent_complete":..,"start_date":..,"estimated_end_date":..}
    note TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by UUID REFERENCES app_users(id) ON DELETE SET NULL,
    decided_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
CREATE INDEX change_requests_status_idx ON change_requests (status, created_at DESC);

-- Schedule edits waiting to be announced to the property's Telegram group
-- in one consolidated "Publish Updates" message (replaces the old
-- Streamlit session-state list, which was lost on reload).
CREATE TABLE pending_schedule_changes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    created_by UUID REFERENCES app_users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);
