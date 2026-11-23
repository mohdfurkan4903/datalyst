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
