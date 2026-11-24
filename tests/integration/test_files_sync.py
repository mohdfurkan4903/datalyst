import pytest

from datalyst.config import Settings
from datalyst.connectors.files import FileStore
from datalyst.core.models import JobStatus, SourceStatus
from datalyst.sync import (
    DataSourceProvider,
    JobContext,
    SyncWorker,
    check_source_request,
)

pytestmark = pytest.mark.integration

CUSTOMERS = "id,full_name,country\n" + "".join(
    f"{i},Customer {i},{'US' if i % 2 else 'DE'}\n" for i in range(1, 51)
)
ORDERS = "id,customer_id,status,placed_on\n" + "".join(
    f"{i},{1 + i % 50},{('new', 'paid', 'shipped')[i % 3]},2024-{1 + i % 12:02d}-15\n"
    for i in range(1, 301)
)


@pytest.fixture
def worker(metastore, tmp_path):
    settings = Settings(files_dir=str(tmp_path / "files"))
    context = JobContext(metastore, DataSourceProvider(settings, metastore), 200)
    worker = SyncWorker(context, worker_id="files-test")
    yield worker
    worker.close()


@pytest.fixture
def source(metastore, tmp_path):
    store = FileStore.create(tmp_path / "files")
    for name, text in (("customers.csv", CUSTOMERS), ("orders.csv", ORDERS)):
        path = tmp_path / name
        path.write_text(text)
        store.add(path, name)
    return metastore.sources.create("uploaded", "files", store.url), store, tmp_path


def run_delayed_jobs(metastore, worker):
    """Relationship inference waits a while after the last sync; run it now."""
    with metastore.pool.connection() as conn:
        conn.execute("UPDATE sync_jobs SET run_after = now()")
    worker.drain()


def test_uploaded_files_are_synced_profiled_and_linked(metastore, worker, source):
    info, _, _ = source

    worker.drain()
    run_delayed_jobs(metastore, worker)

    assert not [
        j for j in metastore.jobs.recent(info.id, 100) if j.status == JobStatus.FAILED
    ]
    assert metastore.sources.get(info.id).status == SourceStatus.READY
    schema = metastore.catalog.load_schema(info.id)
    orders = schema.get_table("main.orders")
    assert orders.row_estimate == 300
    status = next(c for c in orders.columns if c.name == "status")
    assert {v.value for v in status.profile.top_values} == {"new", "paid", "shipped"}
    assert any(
        r["child"].endswith("orders.customer_id")
        and r["parent"].endswith("customers.id")
        for r in metastore.catalog.relationships(info.id)
    )


def test_a_new_file_appears_after_the_next_check(metastore, worker, source):
    info, store, tmp_path = source
    worker.drain()
    version = metastore.catalog.version(info.id)

    path = tmp_path / "products.csv"
    path.write_text("id,name\n1,Pen\n2,Ink\n")
    store.add(path, "products.csv")
    metastore.jobs.enqueue(check_source_request(info.id))
    worker.drain()

    assert metastore.catalog.version(info.id) > version
    tables = {t.name for t in metastore.catalog.load_schema(info.id).tables}
    assert tables == {"customers", "orders", "products"}

    store.remove("products")
    metastore.jobs.enqueue(check_source_request(info.id))
    worker.drain()
    assert {t.name for t in metastore.catalog.load_schema(info.id).tables} == {
        "customers",
        "orders",
    }
