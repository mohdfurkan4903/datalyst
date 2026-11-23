CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE column_values (
    column_id bigint NOT NULL REFERENCES catalog_columns (id) ON DELETE CASCADE,
    value     text   NOT NULL,
    frequency bigint NOT NULL,
    PRIMARY KEY (column_id, value)
);

CREATE INDEX column_values_trgm_idx ON column_values USING gin (value gin_trgm_ops);

ALTER TABLE relationships ADD COLUMN evidence jsonb NOT NULL DEFAULT '{}';
