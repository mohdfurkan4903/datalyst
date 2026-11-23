ALTER TABLE catalog_tables DROP CONSTRAINT catalog_tables_kind_check;

ALTER TABLE catalog_tables
    ADD CONSTRAINT catalog_tables_kind_check
    CHECK (kind IN ('table', 'view', 'materialized_view'));
