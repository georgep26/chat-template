-- Chat history schema for the RAG Lambda.
-- The deployment script can drop/recreate these tables with
-- --reset-chat-history-schema while current chat-history data is non-production.

CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id UUID PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    environment VARCHAR(80) NOT NULL,
    title VARCHAR(255),
    status VARCHAR(40) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    summary_message_count INTEGER NOT NULL DEFAULT 0,
    request_id VARCHAR(255),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chat_history (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE,
    message JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_turn_events (
    event_id UUID PRIMARY KEY,
    trace_id VARCHAR(255),
    conversation_id UUID NOT NULL REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE,
    turn_index INTEGER,
    step_index INTEGER NOT NULL,
    step_name VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    latency_ms INTEGER,
    model_id VARCHAR(255),
    input_summary JSONB,
    output_summary JSONB,
    input_payload JSONB,
    output_payload JSONB,
    error JSONB,
    frontend_event JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_conversations_owner
ON chat_conversations (environment, user_id, conversation_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_conversations_request_id
ON chat_conversations (environment, user_id, request_id)
WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chat_history_conversation_id
ON chat_history (conversation_id, id);

CREATE INDEX IF NOT EXISTS idx_chat_turn_events_conversation_turn
ON chat_turn_events (conversation_id, turn_index, step_index);
