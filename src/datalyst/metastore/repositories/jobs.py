from collections.abc import Iterable
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from datalyst.core.models import Job, JobRequest, JobStatus

MAX_BACKOFF_SECONDS = 3600
BASE_BACKOFF_SECONDS = 10

_COLUMNS = """
id, source_id, kind, target, payload, priority, status, attempts, max_attempts,
run_after, locked_by, error, created_at, finished_at
"""

_ENQUEUE = """
INSERT INTO sync_jobs (source_id, kind, target, payload, priority, max_attempts, run_after)
VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s, now() + coalesce(%s, interval '0')))
ON CONFLICT (source_id, kind, target) WHERE status = 'queued' DO NOTHING
RETURNING id
"""

_CLAIM = f"""
UPDATE sync_jobs
SET status = 'running', attempts = attempts + 1, locked_by = %(worker)s, locked_at = now()
WHERE id = (
    SELECT id FROM sync_jobs
    WHERE status = 'queued' AND run_after <= now()
    ORDER BY priority, run_after, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING {_COLUMNS}
"""


def backoff(attempts: int) -> timedelta:
    seconds = BASE_BACKOFF_SECONDS * 2 ** max(attempts - 1, 0)
    return timedelta(seconds=min(seconds, MAX_BACKOFF_SECONDS))


class JobRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def enqueue(self, request: JobRequest) -> int | None:
        with self._pool.connection() as conn:
            row = conn.execute(_ENQUEUE, _values(request)).fetchone()
        return row["id"] if row else None

    def enqueue_many(self, requests: Iterable[JobRequest]) -> int:
        inserted = 0
        with self._pool.connection() as conn, conn.transaction():
            for request in requests:
                if conn.execute(_ENQUEUE, _values(request)).fetchone():
                    inserted += 1
        return inserted

    def claim(self, worker_id: str) -> Job | None:
        with self._pool.connection() as conn:
            row = conn.execute(_CLAIM, {"worker": worker_id}).fetchone()
        return Job(**row) if row else None

    def complete(self, job_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE sync_jobs
                SET status = 'done', error = NULL, finished_at = now(), locked_at = NULL
                WHERE id = %s
                """,
                (job_id,),
            )

    def fail(self, job: Job, error: str) -> JobStatus:
        if job.attempts >= job.max_attempts:
            self._finish(job.id, JobStatus.FAILED, error)
            return JobStatus.FAILED
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    """
                    UPDATE sync_jobs
                    SET status = 'queued', error = %s, locked_by = NULL, locked_at = NULL,
                        run_after = now() + %s
                    WHERE id = %s
                    """,
                    (error, backoff(job.attempts), job.id),
                )
        except psycopg.errors.UniqueViolation:
            self._finish(job.id, JobStatus.DONE, f"Superseded by a newer job: {error}")
            return JobStatus.DONE
        return JobStatus.QUEUED

    def recover_stale(self, lease: timedelta) -> int:
        recovered = 0
        with self._pool.connection() as conn:
            stale = conn.execute(
                """
                SELECT id FROM sync_jobs
                WHERE status = 'running' AND locked_at < now() - %s
                """,
                (lease,),
            ).fetchall()
        for row in stale:
            try:
                with self._pool.connection() as conn:
                    conn.execute(
                        """
                        UPDATE sync_jobs
                        SET status = CASE WHEN attempts >= max_attempts
                                          THEN 'failed' ELSE 'queued' END,
                            error = 'Worker stopped before finishing',
                            locked_by = NULL, locked_at = NULL, run_after = now()
                        WHERE id = %s AND status = 'running'
                        """,
                        (row["id"],),
                    )
            except psycopg.errors.UniqueViolation:
                self._finish(row["id"], JobStatus.DONE, "Superseded by a newer job")
            recovered += 1
        return recovered

    def counts(self, source_id: int | None = None) -> dict[str, int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT status, count(*) AS n FROM sync_jobs
                WHERE %(source)s::bigint IS NULL OR source_id = %(source)s
                GROUP BY status
                """,
                {"source": source_id},
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def recent(self, source_id: int, limit: int = 20) -> list[Job]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM sync_jobs WHERE source_id = %s
                ORDER BY id DESC LIMIT %s
                """,
                (source_id, limit),
            ).fetchall()
        return [Job(**row) for row in rows]

    def purge(self, older_than: timedelta) -> int:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                DELETE FROM sync_jobs
                WHERE status IN ('done', 'failed') AND finished_at < now() - %s
                """,
                (older_than,),
            ).rowcount

    def heartbeat(self, worker_id: str, hostname: str, pid: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_workers (id, hostname, pid) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET heartbeat_at = now()
                """,
                (worker_id, hostname, pid),
            )

    def remove_worker(self, worker_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM sync_workers WHERE id = %s", (worker_id,))

    def workers(self, alive_within: timedelta = timedelta(minutes=2)) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                SELECT id, hostname, pid, started_at, heartbeat_at FROM sync_workers
                WHERE heartbeat_at > now() - %s ORDER BY started_at
                """,
                (alive_within,),
            ).fetchall()

    def _finish(self, job_id: int, status: JobStatus, error: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE sync_jobs
                SET status = %s, error = %s, finished_at = now(), locked_at = NULL
                WHERE id = %s
                """,
                (status.value, error, job_id),
            )


def _values(request: JobRequest) -> tuple:
    return (
        request.source_id,
        request.kind,
        request.target,
        Jsonb(request.payload),
        request.priority,
        request.max_attempts,
        request.run_after,
        request.delay,
    )
