import threading
from datetime import UTC, datetime, timedelta

import pytest

from datalyst.core.models import JobRequest, JobStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def source(metastore):
    return metastore.sources.create("sales", "postgresql", "postgresql://u@h/db")


def request(source, kind="check_source", target="", **kwargs):
    return JobRequest(source_id=source.id, kind=kind, target=target, **kwargs)


def test_queued_duplicates_are_ignored(metastore, source):
    first = metastore.jobs.enqueue(request(source))

    assert first is not None
    assert metastore.jobs.enqueue(request(source)) is None
    assert metastore.jobs.enqueue(request(source, target="other")) is not None
    assert metastore.jobs.counts(source.id) == {"queued": 2}


def test_a_new_job_can_queue_while_the_same_one_runs(metastore, source):
    metastore.jobs.enqueue(request(source))
    running = metastore.jobs.claim("w1")

    assert running.status == JobStatus.RUNNING
    assert metastore.jobs.enqueue(request(source)) is not None


def test_claim_order_follows_priority(metastore, source):
    metastore.jobs.enqueue(request(source, kind="sync_tables", priority=20))
    metastore.jobs.enqueue(request(source, kind="check_source", priority=10))

    assert metastore.jobs.claim("w").kind == "check_source"
    assert metastore.jobs.claim("w").kind == "sync_tables"
    assert metastore.jobs.claim("w") is None


def test_future_jobs_wait(metastore, source):
    later = datetime.now(UTC) + timedelta(hours=1)
    metastore.jobs.enqueue(request(source, run_after=later))

    assert metastore.jobs.claim("w") is None


def test_concurrent_workers_never_claim_the_same_job(metastore, source):
    for i in range(20):
        metastore.jobs.enqueue(request(source, target=str(i)))
    claimed: list[int] = []
    lock = threading.Lock()

    def work(name: str) -> None:
        while job := metastore.jobs.claim(name):
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 20
    assert len(set(claimed)) == 20


def test_failed_job_is_retried_later_then_gives_up(metastore, source):
    metastore.jobs.enqueue(request(source, max_attempts=2))

    job = metastore.jobs.claim("w")
    assert metastore.jobs.fail(job, "boom") == JobStatus.QUEUED
    assert metastore.jobs.claim("w") is None

    with metastore.pool.connection() as conn:
        conn.execute("UPDATE sync_jobs SET run_after = now()")
    job = metastore.jobs.claim("w")
    assert job.attempts == 2
    assert metastore.jobs.fail(job, "boom again") == JobStatus.FAILED
    [failed] = metastore.jobs.recent(source.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error == "boom again"


def test_retry_is_dropped_when_a_newer_job_is_queued(metastore, source):
    metastore.jobs.enqueue(request(source))
    job = metastore.jobs.claim("w")
    metastore.jobs.enqueue(request(source))

    assert metastore.jobs.fail(job, "boom") == JobStatus.DONE
    assert metastore.jobs.counts(source.id) == {"done": 1, "queued": 1}


def test_jobs_of_a_crashed_worker_are_recovered(metastore, source):
    metastore.jobs.enqueue(request(source))
    metastore.jobs.claim("crashed")
    with metastore.pool.connection() as conn:
        conn.execute("UPDATE sync_jobs SET locked_at = now() - interval '1 hour'")

    assert metastore.jobs.recover_stale(timedelta(minutes=15)) == 1
    job = metastore.jobs.claim("healthy")
    assert job.locked_by == "healthy"
    assert job.error == "Worker stopped before finishing"


def test_complete_and_purge(metastore, source):
    metastore.jobs.enqueue(request(source))
    metastore.jobs.complete(metastore.jobs.claim("w").id)
    assert metastore.jobs.counts(source.id) == {"done": 1}

    with metastore.pool.connection() as conn:
        conn.execute("UPDATE sync_jobs SET finished_at = now() - interval '30 days'")
    assert metastore.jobs.purge(timedelta(days=7)) == 1


def test_worker_heartbeats(metastore):
    metastore.jobs.heartbeat("w1", "host", 42)
    metastore.jobs.heartbeat("w1", "host", 42)

    assert [w["id"] for w in metastore.jobs.workers()] == ["w1"]
    metastore.jobs.remove_worker("w1")
    assert metastore.jobs.workers() == []


def test_due_sources_are_handed_out_once_per_interval(metastore, source):
    assert metastore.sources.due_for_check() == [source.id]
    assert metastore.sources.due_for_check() == []

    metastore.sources.request_check(source.id)
    assert metastore.sources.due_for_check() == [source.id]
    next_check = metastore.sources.get(source.id).next_check_at
    assert next_check > datetime.now(UTC) + timedelta(seconds=250)
