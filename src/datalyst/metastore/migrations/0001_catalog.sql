CREATE TABLE data_sources (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                  text        NOT NULL UNIQUE
                          CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    kind                  text        NOT NULL,
    connection_encrypted  text        NOT NULL,
    include_schemas       text[]      NOT NULL DEFAULT '{}',
    llm_data_policy       text        NOT NULL DEFAULT 'rows'
                          CHECK (llm_data_policy IN ('rows', 'stats', 'names')),
    sync_interval_seconds integer     NOT NULL DEFAULT 300
                          CHECK (sync_interval_seconds >= 60),
    catalog_version       bigint      NOT NULL DEFAULT 0,
    status                text        NOT NULL DEFAULT 'new'
                          CHECK (status IN ('new', 'ready', 'syncing', 'error')),
    status_detail         text,
    last_synced_at        timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalog_tables (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id          bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    schema_name        text        NOT NULL,
    name               text        NOT NULL,
    kind               text        NOT NULL DEFAULT 'table' CHECK (kind IN ('table', 'view')),
    fingerprint        text,
    row_estimate       bigint,
    comment            text,
    description        text,
    description_origin text        CHECK (description_origin IN ('human', 'ai')),
    description_status text        CHECK (description_status IN ('auto', 'approved', 'stale')),
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
    semantic_type      text,
    sample_values      text[]  NOT NULL DEFAULT '{}',
    profile            jsonb   NOT NULL DEFAULT '{}',
    UNIQUE (table_id, name)
);

CREATE TABLE relationships (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id      bigint      NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    from_column_id bigint      NOT NULL REFERENCES catalog_columns (id) ON DELETE CASCADE,
    to_column_id   bigint      NOT NULL REFERENCES catalog_columns (id) ON DELETE CASCADE,
    origin         text        NOT NULL CHECK (origin IN ('declared', 'inferred', 'human')),
    confidence     real        NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    status         text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
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
