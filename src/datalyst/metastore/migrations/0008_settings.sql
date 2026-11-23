CREATE TABLE settings (
    name       text        PRIMARY KEY,
    value      jsonb,
    encrypted  text,
    updated_by bigint      REFERENCES users (id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((value IS NULL) <> (encrypted IS NULL))
);

CREATE TABLE settings_version (
    id      boolean PRIMARY KEY DEFAULT true CHECK (id),
    version bigint  NOT NULL DEFAULT 0
);

INSERT INTO settings_version DEFAULT VALUES;
