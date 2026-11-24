import pytest

from datalyst.catalog.sqlrefs import catalog_problems, tables_in_sql
from datalyst.config import Settings
from datalyst.core.models import JobStatus, SourceStatus
from datalyst.sync import DataSourceProvider, JobContext, SyncWorker

pytestmark = pytest.mark.integration


def run_delayed_jobs(metastore, worker):
    """Relationship inference waits a while after the last sync; run it now."""
    with metastore.pool.connection() as conn:
        conn.execute("UPDATE sync_jobs SET run_after = now()")
    worker.drain()


@pytest.fixture
def worker(metastore):
    context = JobContext(metastore, DataSourceProvider(Settings(), metastore), 200)
    worker = SyncWorker(context, worker_id="mysql-test")
    yield worker
    worker.close()


def test_a_mysql_source_is_synced_profiled_and_linked(metastore, worker, mysql_shop):
    db = mysql_shop["database"]
    source = metastore.sources.create("shop-mysql", "mysql", mysql_shop["read_only"])

    worker.drain()
    run_delayed_jobs(metastore, worker)

    jobs = metastore.jobs.recent(source.id, 100)
    assert not [j for j in jobs if j.status == JobStatus.FAILED]
    assert metastore.sources.get(source.id).status == SourceStatus.READY
    schema = metastore.catalog.load_schema(source.id)
    assert {t.qualified_name for t in schema.tables} >= {
        f"{db}.customers",
        f"{db}.orders",
        f"{db}.order_items",
        f"{db}.products",
    }
    orders = schema.get_table(f"{db}.orders")
    status = next(c for c in orders.columns if c.name == "status")
    assert {v.value for v in status.profile.top_values} == {
        "pending",
        "paid",
        "shipped",
        "cancelled",
    }
    relationships = metastore.catalog.relationships(source.id)
    # A declared key, and one that has to be inferred from names and values.
    for child in ("orders.customer_id", "visits.customer_id"):
        assert any(
            r["child"].endswith(child) and r["parent"].endswith("customers.id")
            for r in relationships
        ), child
    visits = next(r for r in relationships if r["child"].endswith("visits.customer_id"))
    assert visits["origin"] == "inferred"


def test_catalog_checks_read_backtick_queries(metastore, worker, mysql_shop):
    db = mysql_shop["database"]
    source = metastore.sources.create("shop-mysql", "mysql", mysql_shop["read_only"])
    worker.drain()
    schema = metastore.catalog.load_schema(source.id)

    query = f"SELECT `o`.`status` FROM `{db}`.`orders` AS `o` JOIN `customers` c ON c.id = o.customer_id"

    assert tables_in_sql(query, schema, "mysql", db) == [
        f"{db}.orders",
        f"{db}.customers",
    ]
    assert catalog_problems(query, schema, "mysql", db) == []
    problems = catalog_problems("SELECT `nope` FROM `orders`", schema, "mysql", db)
    assert problems and "nope" in problems[0]
