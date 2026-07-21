CREATE TABLE draw_milestones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    linked_line_item_id UUID REFERENCES line_items(id) ON DELETE SET NULL,
    milestone_name VARCHAR(255) NOT NULL,
    draw_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    status VARCHAR(50) DEFAULT 'Pending',
    released_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);

CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
    linked_line_item_id UUID REFERENCES line_items(id) ON DELETE SET NULL,
    telegram_message_id BIGINT,
    telegram_chat_id VARCHAR(100),
    author_name VARCHAR(255),
    message_text TEXT,
    photo_file_id VARCHAR(255),
    posted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW()),
    UNIQUE (telegram_chat_id, telegram_message_id)
);
