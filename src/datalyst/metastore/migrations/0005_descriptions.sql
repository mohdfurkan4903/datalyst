CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE catalog_tables
    ADD COLUMN synonyms          text[] NOT NULL DEFAULT '{}',
    ADD COLUMN example_questions text[] NOT NULL DEFAULT '{}',
    ADD COLUMN description_hash  text;

ALTER TABLE catalog_columns
    ADD COLUMN synonyms text[] NOT NULL DEFAULT '{}';

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
