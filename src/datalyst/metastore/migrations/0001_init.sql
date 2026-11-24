CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- Installation ---------------------------------------------------------------

CREATE TABLE instance (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),
    signing_key text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO instance (signing_key)
VALUES (replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''));

-- People ---------------------------------------------------------------------

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

-- Data sources ---------------------------------------------------------------

CREATE TABLE data_sources (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                  text        NOT NULL UNIQUE
                          CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    kind                  text        NOT NULL,
    connection_url        text        NOT NULL,
    include_schemas       text[]      NOT NULL DEFAULT '{}',
    llm_data_policy       text        NOT NULL DEFAULT 'rows'
                          CHECK (llm_data_policy IN ('rows', 'stats', 'names')),
    open_to_all           boolean     NOT NULL DEFAULT true,
    sync_interval_seconds integer     NOT NULL DEFAULT 300
                          CHECK (sync_interval_seconds >= 60),
    catalog_version       bigint      NOT NULL DEFAULT 0,
    status                text        NOT NULL DEFAULT 'new'
                          CHECK (status IN ('new', 'ready', 'syncing', 'error')),
    status_detail         text,
    last_synced_at        timestamptz,
    last_checked_at       timestamptz,
    next_check_at         timestamptz NOT NULL DEFAULT now(),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- Users who may use a source that is not open to all.
CREATE TABLE source_users (
    source_id bigint NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    user_id   bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    PRIMARY KEY (source_id, user_id)
);

CREATE INDEX source_users_user_idx ON source_users (user_id);

-- Catalog --------------------------------------------------------------------

CREATE TABLE catalog_tables (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id          bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    schema_name        text        NOT NULL,
    name               text        NOT NULL,
    kind               text        NOT NULL DEFAULT 'table'
                       CHECK (kind IN ('table', 'view', 'materialized_view')),
    fingerprint        text,
    row_estimate       bigint,
    comment            text,
    description        text,
    description_origin text        CHECK (description_origin IN ('human', 'ai')),
    description_status text        CHECK (description_status IN ('auto', 'approved', 'stale')),
    description_hash   text,
    synonyms           text[]      NOT NULL DEFAULT '{}',
    example_questions  text[]      NOT NULL DEFAULT '{}',
    synced_at          timestamptz,
    profiled_at        timestamptz,
    described_at       timestamptz,
    removed_at         timestamptz,
    UNIQUE (source_id, schema_name, name)
);

CREATE TABLE catalog_columns (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_id           bigint  NOT NULL REFERENCES catalog_tables (id) ON DELETE CASCADE,
    ordinal            integer NOT NULL,
    name               text    NOT NULL,
    data_type          text    NOT NULL,
    nullable           boolean NOT NULL DEFAULT true,
    is_primary_key     boolean NOT NULL DEFAULT false,
    comment            text,
    description        text,
    description_origin text    CHECK (description_origin IN ('human', 'ai')),
    description_status text    CHECK (description_status IN ('auto', 'approved', 'stale')),
    synonyms           text[]  NOT NULL DEFAULT '{}',
    semantic_type      text,
    sample_values      text[]  NOT NULL DEFAULT '{}',
    profile            jsonb   NOT NULL DEFAULT '{}',
    UNIQUE (table_id, name)
);

CREATE TABLE column_values (
    column_id bigint NOT NULL REFERENCES catalog_columns (id) ON DELETE CASCADE,
    value     text   NOT NULL,
    frequency bigint NOT NULL,
    PRIMARY KEY (column_id, value)
);

CREATE INDEX column_values_trgm_idx ON column_values USING gin (value gin_trgm_ops);

CREATE TABLE relationships (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id      bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    from_column_id bigint      NOT NULL REFERENCES catalog_columns (id) ON DELETE CASCADE,
    to_column_id   bigint      NOT NULL REFERENCES catalog_columns (id) ON DELETE CASCADE,
    origin         text        NOT NULL CHECK (origin IN ('declared', 'inferred', 'human')),
    confidence     real        NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    status         text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    evidence       jsonb       NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (from_column_id, to_column_id)
);

CREATE INDEX relationships_source_idx ON relationships (source_id);

CREATE TABLE schema_changes (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    schema_name text        NOT NULL,
    table_name  text        NOT NULL,
    change      text        NOT NULL CHECK (change IN ('added', 'removed', 'altered')),
    details     jsonb       NOT NULL DEFAULT '{}',
    detected_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX schema_changes_source_idx ON schema_changes (source_id, detected_at DESC);

CREATE TABLE embeddings (
    source_id    bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    object_type  text        NOT NULL CHECK (object_type IN ('table', 'column', 'query')),
    object_id    bigint      NOT NULL,
    model        text        NOT NULL,
    content_hash text        NOT NULL,
    embedding    vector      NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, object_type, object_id, model)
);

-- Business knowledge ---------------------------------------------------------

CREATE TABLE glossary_terms (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id  bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    term       text        NOT NULL,
    definition text        NOT NULL,
    sql_hint   text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, term)
);

CREATE TABLE verified_queries (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    question    text        NOT NULL,
    sql         text        NOT NULL,
    tables      text[]      NOT NULL DEFAULT '{}',
    status      text        NOT NULL DEFAULT 'approved'
                CHECK (status IN ('approved', 'pending', 'rejected')),
    created_by  text,
    approved_by text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX verified_queries_source_idx ON verified_queries (source_id, status);

-- Background work ------------------------------------------------------------

CREATE TABLE sync_jobs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id    bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    kind         text        NOT NULL,
    target       text        NOT NULL DEFAULT '',
    payload      jsonb       NOT NULL DEFAULT '{}',
    priority     integer     NOT NULL DEFAULT 100,
    status       text        NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts     integer     NOT NULL DEFAULT 0,
    max_attempts integer     NOT NULL DEFAULT 5,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_by    text,
    locked_at    timestamptz,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);

CREATE UNIQUE INDEX sync_jobs_queued_unique
    ON sync_jobs (source_id, kind, target) WHERE status = 'queued';

CREATE INDEX sync_jobs_ready_idx
    ON sync_jobs (priority, run_after, id) WHERE status = 'queued';

CREATE INDEX sync_jobs_running_idx
    ON sync_jobs (locked_at) WHERE status = 'running';

CREATE INDEX sync_jobs_source_idx ON sync_jobs (source_id, created_at DESC);

CREATE TABLE sync_workers (
    id           text PRIMARY KEY,
    hostname     text        NOT NULL,
    pid          integer     NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);

-- Activity -------------------------------------------------------------------

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

CREATE TABLE llm_usage (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id     bigint REFERENCES data_sources (id) ON DELETE SET NULL,
    purpose       text        NOT NULL,
    model         text        NOT NULL,
    input_tokens  integer,
    output_tokens integer,
    duration_ms   integer,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX llm_usage_created_idx ON llm_usage (created_at DESC);

-- Settings edited in the web UI ----------------------------------------------

CREATE TABLE settings (
    name       text        PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_by bigint      REFERENCES users (id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE settings_version (
    id      boolean PRIMARY KEY DEFAULT true CHECK (id),
    version bigint  NOT NULL DEFAULT 0
);

INSERT INTO settings_version DEFAULT VALUES;
