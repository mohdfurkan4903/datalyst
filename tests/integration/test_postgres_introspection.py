import uuid

import pytest

from datalyst.connectors.postgres import PostgresDataSource
from datalyst.core.exceptions import QueryExecutionError
from datalyst.core.interfaces import CatalogIntrospector
from datalyst.core.models import ColumnRef, TableKind, TableRef

pytestmark = pytest.mark.integration

SHOP_TABLES = {"customers", "orders", "order_items", "products"}


def ref(name: str) -> TableRef:
    return TableRef(schema_name="public", name=name)


def col(table: str, column: str) -> ColumnRef:
    return ColumnRef(table=ref(table), column=column)


def by_name(fingerprints):
    return {f.table.name: f for f in fingerprints}


@pytest.fixture
def scratch(shop_admin):
    name = f"probe_{uuid.uuid4().hex[:8]}"
    created: list[str] = []

    def run(statement: str, *objects: str) -> None:
        shop_admin.execute(statement.format(name=name))
        created.extend(o.format(name=name) for o in objects)

    yield name, run
    for obj in reversed(created):
        shop_admin.execute(f"DROP {obj} CASCADE")


def test_postgres_source_is_an_introspector(datasource):
    assert isinstance(datasource, CatalogIntrospector)


def test_fingerprints_cover_tables_and_are_stable(datasource):
    first = by_name(datasource.fingerprints())

    assert set(first) >= SHOP_TABLES
    assert first["orders"].kind == TableKind.TABLE
    assert first["orders"].row_estimate == 2000
    assert by_name(datasource.fingerprints()) == first


def test_alter_changes_only_that_tables_fingerprint(datasource, scratch):
    name, run = scratch
    run("CREATE TABLE {name} (id int PRIMARY KEY)", "TABLE IF EXISTS {name}")
    before = by_name(datasource.fingerprints())

    run("ALTER TABLE {name} ADD COLUMN note text")
    added = by_name(datasource.fingerprints())
    run("COMMENT ON COLUMN {name}.note IS 'free text'")
    commented = by_name(datasource.fingerprints())

    assert added[name].fingerprint != before[name].fingerprint
    assert commented[name].fingerprint != added[name].fingerprint
    for table in SHOP_TABLES:
        assert commented[table].fingerprint == before[table].fingerprint

    run("DROP TABLE {name}")
    assert name not in by_name(datasource.fingerprints())


def test_views_and_materialized_views_are_listed(datasource, scratch):
    name, run = scratch
    run(
        "CREATE VIEW {name}_v AS SELECT id, status FROM orders",
        "VIEW IF EXISTS {name}_v",
    )
    run(
        "CREATE MATERIALIZED VIEW {name}_m AS SELECT status, count(*) AS n "
        "FROM orders GROUP BY status",
        "MATERIALIZED VIEW IF EXISTS {name}_m",
    )

    kinds = {f.table.name: f.kind for f in datasource.fingerprints()}
    assert kinds[f"{name}_v"] == TableKind.VIEW
    assert kinds[f"{name}_m"] == TableKind.MATERIALIZED_VIEW
    [view] = datasource.describe_tables([ref(f"{name}_v")])
    assert [c.name for c in view.columns] == ["id", "status"]
    assert datasource.sample_rows(ref(f"{name}_m"), 2).row_count == 2


def test_partitions_are_hidden_behind_their_parent(datasource, scratch):
    name, run = scratch
    run(
        "CREATE TABLE {name} (id int, region text) PARTITION BY LIST (region)",
        "TABLE IF EXISTS {name}",
    )
    run("CREATE TABLE {name}_eu PARTITION OF {name} FOR VALUES IN ('eu')")

    names = set(by_name(datasource.fingerprints()))
    assert name in names
    assert f"{name}_eu" not in names


def test_describe_only_requested_tables(datasource):
    tables = datasource.describe_tables([ref("orders"), ref("missing")])

    assert [t.name for t in tables] == ["orders"]
    assert tables[0].row_estimate == 2000
    assert datasource.describe_tables([]) == []


def test_include_schemas_limits_visibility(database_url):
    with PostgresDataSource(database_url, include_schemas=["nope"]) as ds:
        assert ds.fingerprints() == []
        assert ds.describe_tables() == []
    with PostgresDataSource(database_url, include_schemas=["public"]) as ds:
        assert set(by_name(ds.fingerprints())) >= SHOP_TABLES


def test_column_stats(datasource):
    stats = {s.column: s for s in datasource.column_stats(ref("orders"))}

    assert stats["status"].distinct == 4
    assert set(stats["status"].common_values) == {
        "pending",
        "paid",
        "shipped",
        "cancelled",
    }
    assert stats["id"].distinct == 2000
    assert stats["status"].null_fraction == 0


def test_sample_rows_respects_limit(datasource):
    result = datasource.sample_rows(ref("customers"), 5)

    assert result.row_count == 5
    assert result.columns == ["id", "name", "email", "country", "created_at"]


def test_top_values(datasource):
    values = datasource.top_values(col("orders", "status"), limit=2)

    assert len(values) == 2
    assert values[0].count >= values[1].count
    assert {v.value for v in values} <= {"pending", "paid", "shipped", "cancelled"}


def test_containment(datasource):
    forward = datasource.containment(
        col("orders", "customer_id"), col("customers", "id")
    )
    backward = datasource.containment(
        col("customers", "id"), col("orders", "customer_id")
    )

    assert forward.sampled == 200
    assert forward.ratio == 1.0
    assert 0 < backward.ratio <= 1.0


def test_unknown_table_is_an_execution_error(datasource):
    with pytest.raises(QueryExecutionError, match="does not exist"):
        datasource.sample_rows(ref("missing"))


def test_identifiers_are_quoted(datasource):
    with pytest.raises(QueryExecutionError):
        datasource.top_values(col("orders", "status; DROP TABLE orders"))
    assert datasource.execute("SELECT count(*) FROM public.orders").rows == [[2000]]
