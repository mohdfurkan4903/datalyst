ALTER TABLE data_sources
    ADD COLUMN next_check_at   timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN last_checked_at timestamptz;

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
