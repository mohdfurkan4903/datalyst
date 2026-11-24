import time
from datetime import date

import pytest

from datalyst.config import Settings
from datalyst.connectors import create_datasource, datasource_kind
from datalyst.connectors.files import FilesDataSource, FilesQueryValidator, FileStore
from datalyst.connectors.files.importer import column_names, table_name
from datalyst.core.exceptions import (
    ConfigurationError,
    NotFoundError,
    QueryExecutionError,
    UnsafeQueryError,
)
from datalyst.core.models import ColumnRef, TableRef

ORDERS = """\
Order ID,Customer,Order Date,Total ($),Status
1,Ann,2024-01-05,10.50,paid
2,Bob,2024-02-11,20.00,shipped
3,Ann,2024-03-01,7.25,paid
"""
CUSTOMERS = "id,name,country\n1,Ann,US\n2,Bob,DE\n"


@pytest.fixture
def store(tmp_path):
    return FileStore.create(tmp_path / "files")


def upload(tmp_path, store, text, filename, encoding="utf-8"):
    path = tmp_path / "incoming.csv"
    path.write_bytes(text.encode(encoding))
    return store.add(path, filename)


@pytest.fixture
def source(tmp_path, store):
    upload(tmp_path, store, ORDERS, "Orders.csv")
    upload(tmp_path, store, CUSTOMERS, "customers.csv")
    with FilesDataSource(store.url, tmp_path / "files", max_rows=50) as source:
        yield source


@pytest.mark.parametrize(
    ("filename", "table"),
    [
        ("Orders.csv", "orders"),
        ("Q1 Sales (final).csv", "q1_sales_final"),
        ("2024-data.csv", "t_2024_data"),
        ("---.csv", "table"),
    ],
)
def test_table_names(filename, table):
    assert table_name(filename) == table


def test_column_names_are_clean_and_unique():
    assert column_names(
        ["Order ID", "Total ($)", "total", "total", "", "2nd", "ID"]
    ) == [
        "order_id",
        "total",
        "total_2",
        "total_3",
        "column_5",
        "c_2nd",
        "id",
    ]


def test_csv_becomes_a_typed_table(tmp_path, store):
    imported = upload(tmp_path, store, ORDERS, "orders.csv")

    assert imported[0].name == "orders"
    assert imported[0].rows == 3
    assert imported[0].columns == [
        "order_id",
        "customer",
        "order_date",
        "total",
        "status",
    ]
    assert imported[0].replaced is False
    assert upload(tmp_path, store, ORDERS, "orders.csv")[0].replaced is True


def test_schema_is_read_from_the_files(source):
    schema = source.get_schema()

    assert [t.qualified_name for t in schema.tables] == [
        "main.customers",
        "main.orders",
    ]
    orders = schema.get_table("main.orders")
    assert orders.row_estimate == 3
    assert {c.name: c.data_type for c in orders.columns} == {
        "order_id": "BIGINT",
        "customer": "VARCHAR",
        "order_date": "DATE",
        "total": "DOUBLE",
        "status": "VARCHAR",
    }


def test_runs_queries(source):
    result = source.execute(
        "SELECT customer, SUM(total) AS spent FROM main.orders GROUP BY 1 ORDER BY 1"
    )

    assert result.columns == ["customer", "spent"]
    assert result.rows == [["Ann", 17.75], ["Bob", 20.0]]
    assert source.execute("SELECT order_date FROM orders LIMIT 1").rows == [
        [date(2024, 1, 5)]
    ]


def test_semicolon_and_other_delimiters(tmp_path, store):
    imported = upload(tmp_path, store, "a;b\n1;x\n2;y\n", "semi.csv")
    tabs = upload(tmp_path, store, "a\tb\n1\tx\n", "tabs.tsv")

    assert imported[0].columns == ["a", "b"]
    assert tabs[0].columns == ["a", "b"]


def test_older_encodings_are_read(tmp_path, store):
    upload(tmp_path, store, "city,n\nMünchen,1\nZürich,2\n", "cities.csv", "latin-1")
    with FilesDataSource(store.url, store.directory.parent) as source:
        assert source.execute("SELECT city FROM cities ORDER BY 1").rows == [
            ["München"],
            ["Zürich"],
        ]


def test_messy_files_still_import_as_text(tmp_path, store):
    # Numbers for longer than the type guess looks at, then a word.
    messy = "id,value\n" + "4,40\n" * 25000 + "5,n/a\n"
    imported = upload(tmp_path, store, messy, "messy.csv")

    assert imported[0].rows == 25001
    with FilesDataSource(store.url, store.directory.parent) as source:
        assert source.execute("SELECT COUNT(*) FROM messy").rows == [[25001]]
        assert source.execute("SELECT MAX(value) FROM messy").rows == [["n/a"]]


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        ("id,name\n", "empty.csv", "no data rows"),
        ("", "blank.csv", "no data rows"),
        ("a,b\n1,2\n", "notes.pdf", "Cannot read"),
        ("a,b\n1,2\n", "old.xls", "old Excel format"),
        ("a,b\n1,2\n", "noext", "Cannot read"),
    ],
)
def test_bad_uploads_are_rejected(tmp_path, store, content, filename, message):
    with pytest.raises(ConfigurationError, match=message):
        upload(tmp_path, store, content, filename)
    assert store.files() == []
    assert [p.name for p in store.directory.iterdir()] == []


def test_new_and_replaced_files_are_picked_up_without_a_restart(
    tmp_path, store, source
):
    before = {f.table.name: f.fingerprint for f in source.fingerprints()}
    upload(tmp_path, store, "id,sku\n1,a\n", "products.csv")
    time.sleep(0.01)
    upload(tmp_path, store, CUSTOMERS + "3,Cy,FR\n", "customers.csv")

    after = {f.table.name: f.fingerprint for f in source.fingerprints()}
    assert set(after) == {"customers", "orders", "products"}
    assert after["customers"] != before["customers"]
    assert after["orders"] == before["orders"]
    assert source.execute("SELECT COUNT(*) FROM customers").rows == [[3]]

    store.remove("products")
    assert "products" not in {t.name for t in source.describe_tables()}
    with pytest.raises(QueryExecutionError):
        source.execute("SELECT * FROM products")


def test_describes_only_the_tables_asked_for(source):
    tables = source.describe_tables([TableRef(schema_name="main", name="orders")])

    assert [t.name for t in tables] == ["orders"]
    assert source.describe_tables([]) == []


def test_profiling_queries(source):
    orders = TableRef(schema_name="main", name="orders")
    customers = TableRef(schema_name="main", name="customers")

    sample = source.sample_rows(orders, 2)
    assert sample.row_count == 2
    assert sample.columns[0] == "order_id"
    values = source.top_values(ColumnRef(table=orders, column="status"))
    assert [(v.value, v.count) for v in values] == [("paid", 2), ("shipped", 1)]
    joined = source.containment(
        ColumnRef(table=orders, column="customer"),
        ColumnRef(table=customers, column="name"),
    )
    assert joined.ratio == 1.0
    assert source.column_stats(orders) == []


def test_results_are_capped(tmp_path, store):
    upload(
        tmp_path,
        store,
        "n\n" + "\n".join(str(i) for i in range(200)) + "\n",
        "nums.csv",
    )
    with FilesDataSource(store.url, store.directory.parent, max_rows=10) as source:
        result = source.execute("SELECT n FROM nums")

    assert result.row_count == 10 and result.truncated


def test_long_queries_are_stopped(tmp_path, store):
    upload(tmp_path, store, "n\n1\n", "one.csv")
    with FilesDataSource(
        store.url, store.directory.parent, timeout_seconds=0.5
    ) as source:
        with pytest.raises(QueryExecutionError, match="stopped"):
            source.execute(
                "SELECT SUM(a.range * b.range) FROM range(100000) a, range(100000) b"
            )
        assert source.execute("SELECT 1").rows == [[1]]


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('/tmp/x.parquet')",
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM glob('/*')",
        "SELECT * FROM '/etc/passwd'",
        "SELECT * FROM duckdb_settings()",
        "SELECT getenv('HOME')",
        "COPY orders TO '/tmp/out.csv'",
        "ATTACH '/tmp/x.db'",
        "INSTALL httpfs",
        "SET enable_external_access = true",
        "PRAGMA database_list",
        "DELETE FROM orders",
        "DROP VIEW orders",
        "SELECT 1; SELECT 2",
    ],
)
def test_nothing_but_the_uploaded_tables_can_be_read(source, query):
    with pytest.raises(UnsafeQueryError):
        source.validate(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM glob('/*')",
        "SELECT * FROM '/etc/hosts'",
        "COPY orders TO '/tmp/datalyst_out.csv'",
        "ATTACH '/tmp/datalyst.db'",
        "SET enable_external_access = true",
        "INSTALL httpfs",
    ],
)
def test_duckdb_itself_is_locked_down_when_the_validator_is_bypassed(source, query):
    with pytest.raises(QueryExecutionError):
        source.execute(query)


def test_validator_allows_ordinary_analysis():
    validator = FilesQueryValidator()

    for query in (
        "SELECT * FROM main.orders",
        "SELECT strftime(order_date, '%Y-%m') AS m, COUNT(*) FROM orders GROUP BY 1",
        "SELECT * FROM generate_series(1, 3)",
        "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t",
        "FROM orders SELECT customer",
    ):
        assert validator.validate(query)


def test_folder_must_be_an_uploaded_source(tmp_path):
    for url in ("files://..", "files://../../etc", "files://sales", "files://"):
        with pytest.raises(ConfigurationError):
            FilesDataSource(url, tmp_path)
        with pytest.raises(ConfigurationError):
            FileStore.from_url(tmp_path, url)
    with pytest.raises(ConfigurationError):
        FileStore.from_url(tmp_path, "postgresql://x/y")


def test_store_lists_removes_and_destroys(tmp_path, store):
    upload(tmp_path, store, CUSTOMERS, "customers.csv")

    assert [(f.table, f.size_bytes > 0) for f in store.files()] == [("customers", True)]
    with pytest.raises(NotFoundError):
        store.remove("nope")
    with pytest.raises(NotFoundError):
        store.remove("../customers")
    store.remove("customers")
    assert store.files() == []
    store.destroy()
    assert not store.directory.exists()


def test_registered_as_a_connector(tmp_path, store):
    assert datasource_kind(store.url) == "files"
    with create_datasource(
        store.url, Settings(files_dir=str(tmp_path / "files"))
    ) as source:
        assert isinstance(source, FilesDataSource)
        assert (source.sql_dialect, source.default_schema) == ("duckdb", "main")
        source.check()
    with (
        create_datasource(
            f"files://{'0' * 32}", Settings(files_dir=str(tmp_path / "files"))
        ) as missing,
        pytest.raises(QueryExecutionError, match="missing"),
    ):
        missing.check()
