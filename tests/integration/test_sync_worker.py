import uuid

import pytest

from datalyst.config import Settings
from datalyst.core.models import JobRequest, JobStatus, SourceStatus
from datalyst.sync import (
    CHECK_SOURCE,
    INFER_RELATIONSHIPS,
    SYNC_TABLES,
    DataSourceProvider,
    JobContext,
    SyncWorker,
    check_source_request,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def make_worker(metastore, metastore_url):
    workers = []

    def make(batch_size=200):
        settings = Settings(_env_file=None, metastore_url=metastore_url)
        context = JobContext(
            metastore, DataSourceProvider(settings, metastore), batch_size
        )
        worker = SyncWorker(context, worker_id=f"test-{len(workers)}")
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.close()


@pytest.fixture
def shop(metastore, database_url):
    return metastore.sources.create("shop", "postgresql", database_url)


def jobs_by_kind(metastore, source_id):
    jobs = metastore.jobs.recent(source_id, 100)
    return {
        kind: [j for j in jobs if j.kind == kind]
        for kind in (CHECK_SOURCE, SYNC_TABLES)
    }


def test_new_source_is_scheduled_and_synced(metastore, shop, make_worker):
    worker = make_worker(batch_size=3)

    processed = worker.drain()

    jobs = jobs_by_kind(metastore, shop.id)
    assert len(jobs[CHECK_SOURCE]) == 1
    assert len(jobs[SYNC_TABLES]) == 2
    assert processed >= 3
    for job in metastore.jobs.recent(shop.id, 100):
        expected = (
            JobStatus.QUEUED if job.kind == INFER_RELATIONSHIPS else JobStatus.DONE
        )
        assert job.status == expected, job.kind
    schema = metastore.catalog.load_schema(shop.id)
    assert {"customers", "orders", "order_items", "products"} <= {
        t.name for t in schema.tables
    }
    info = metastore.sources.get(shop.id)
    assert info.status == SourceStatus.READY
    assert info.catalog_version >= 1


def test_unchanged_source_only_runs_the_check(metastore, shop, make_worker):
    worker = make_worker()
    worker.drain()
    version = metastore.catalog.version(shop.id)

    metastore.jobs.enqueue(check_source_request(shop.id))
    assert worker.drain() == 1
    assert metastore.catalog.version(shop.id) == version


def test_alter_resyncs_only_the_changed_table(metastore, shop, make_worker, shop_admin):
    name = f"probe_{uuid.uuid4().hex[:8]}"
    shop_admin.execute(f"CREATE TABLE {name} (id int PRIMARY KEY)")
    try:
        worker = make_worker()
        worker.drain()

        shop_admin.execute(f"ALTER TABLE {name} ADD COLUMN note text")
        metastore.jobs.enqueue(check_source_request(shop.id))
        worker.drain()

        latest_sync = jobs_by_kind(metastore, shop.id)[SYNC_TABLES][0]
        assert [entry[1] for entry in latest_sync.payload["tables"]] == [name]
        table = metastore.catalog.load_schema(shop.id).get_table(f"public.{name}")
        assert [c.name for c in table.columns] == ["id", "note"]
        [change] = metastore.catalog.recent_changes(shop.id, 1)
        assert (change["table_name"], change["change"]) == (name, "altered")
        assert change["details"] == {"added": ["note"]}
    finally:
        shop_admin.execute(f"DROP TABLE IF EXISTS {name}")

    metastore.jobs.enqueue(check_source_request(shop.id))
    worker.drain()
    assert metastore.catalog.load_schema(shop.id).get_table(f"public.{name}") is None
    [change] = metastore.catalog.recent_changes(shop.id, 1)
    assert change["change"] == "removed"


def test_unreachable_source_is_marked_and_retried(metastore, make_worker):
    broken = metastore.sources.create(
        "broken",
        "postgresql",
        "postgresql://nobody:x@localhost:1/none?connect_timeout=1",
    )
    worker = make_worker()

    worker.drain()

    info = metastore.sources.get(broken.id)
    assert info.status == SourceStatus.ERROR
    assert info.status_detail
    [job] = metastore.jobs.recent(broken.id)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1


def test_unknown_job_kind_fails_without_stopping_the_worker(
    metastore, shop, make_worker
):
    metastore.jobs.enqueue(
        JobRequest(source_id=shop.id, kind="nonsense", priority=1, max_attempts=1)
    )
    worker = make_worker()

    worker.drain()

    kinds = {j.kind: j.status for j in metastore.jobs.recent(shop.id, 100)}
    assert kinds["nonsense"] == JobStatus.FAILED
    assert kinds[CHECK_SOURCE] == JobStatus.DONE
