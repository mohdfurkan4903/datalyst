CREATE TABLE users (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         text        NOT NULL,
    name          text        NOT NULL DEFAULT '',
    password_hash text        NOT NULL,
    role          text        NOT NULL DEFAULT 'analyst' CHECK (role IN ('admin', 'analyst')),
    active        boolean     NOT NULL DEFAULT true,
    token_version integer     NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

CREATE UNIQUE INDEX users_email_unique ON users (lower(email));

CREATE TABLE groups (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text        NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    group_id bigint NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    user_id  bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE source_grants (
    group_id       bigint NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    source_id      bigint NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    table_patterns text[],
    PRIMARY KEY (group_id, source_id)
);

CREATE TABLE query_history (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id            bigint REFERENCES users (id) ON DELETE SET NULL,
    source_id          bigint REFERENCES data_sources (id) ON DELETE CASCADE,
    question           text        NOT NULL,
    rewritten_question text,
    tables             text[]      NOT NULL DEFAULT '{}',
    sql                text,
    status             text        NOT NULL CHECK (status IN ('success', 'error', 'declined')),
    error              text,
    attempts           integer     NOT NULL DEFAULT 0,
    row_count          integer,
    duration_ms        integer,
    feedback           smallint CHECK (feedback IN (-1, 1)),
    feedback_note      text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX query_history_user_idx ON query_history (user_id, created_at DESC);
CREATE INDEX query_history_source_idx ON query_history (source_id, created_at DESC);
