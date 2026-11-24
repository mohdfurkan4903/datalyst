-- Who uploaded a data source. Users can upload their own files; those sources are
-- private to them (and to admins) unless an admin shares them.

ALTER TABLE data_sources
    ADD COLUMN owner_id bigint REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX data_sources_owner_idx ON data_sources (owner_id);
