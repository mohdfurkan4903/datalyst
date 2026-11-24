-- Chats (session memory) and what the agent remembers about each user
-- (long-term memory).

CREATE TABLE conversations (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    source_id  bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    title      text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX conversations_user_idx ON conversations (user_id, source_id, updated_at DESC);

ALTER TABLE query_history
    ADD COLUMN conversation_id bigint REFERENCES conversations (id) ON DELETE SET NULL,
    ADD COLUMN result_preview  text;

CREATE INDEX query_history_conversation_idx ON query_history (conversation_id, id);

-- source_id NULL means the memory applies to every data source.
CREATE TABLE memories (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    source_id  bigint      REFERENCES data_sources (id) ON DELETE CASCADE,
    text       text        NOT NULL CHECK (char_length(text) BETWEEN 1 AND 300),
    origin     text        NOT NULL CHECK (origin IN ('user', 'agent')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX memories_user_idx ON memories (user_id, updated_at DESC);
