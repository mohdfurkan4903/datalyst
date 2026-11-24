import pytest

from datalyst.config import Settings
from datalyst.connectors import create_datasource, datasource_kind, supported_schemes
from datalyst.connectors.mysql import MySQLDataSource, MySQLQueryValidator
from datalyst.connectors.mysql.connector import Connection
from datalyst.connectors.mysql.introspection import (
    build_fingerprints,
    build_schema,
    enum_values,
    scope,
)
from datalyst.connectors.mysql.profiling import identifier, sample_rows_sql
from datalyst.core.exceptions import UnsafeQueryError
from datalyst.core.models import TableKind, TableRef
from datalyst.enrichment.profiler import type_family

validator = MySQLQueryValidator()


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "SELECT `id` FROM `shop`.`orders` LIMIT 10;",
        "SELECT c.name, COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY 1",
        "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t",
        "SELECT DATE_FORMAT(ordered_at, '%Y-%m') AS month FROM orders",
        "SELECT STR_TO_DATE(d, '%d/%m/%Y') FROM t",
    ],
)
def test_validator_allows_reads(query):
    assert validator.validate(query)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "DELETE FROM orders",
        "UPDATE orders SET status = 'paid'",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "SHOW TABLES",
        "CALL cleanup()",
        "SET GLOBAL read_only = 0",
        "SELECT 1; DROP TABLE orders",
        "SELECT SLEEP(10)",
        "SELECT BENCHMARK(100000000, MD5('x'))",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT GET_LOCK('a', 10)",
        "SELECT * FROM orders INTO OUTFILE '/tmp/orders'",
        "SELECT * FROM orders INTO DUMPFILE '/tmp/orders'",
        "SELECT * FROM orders FOR UPDATE",
        "SELECT * FROM orders LOCK IN SHARE MODE",
        "SELECT * INTO @everything FROM orders",
        "LOAD DATA INFILE '/tmp/x' INTO TABLE orders",
    ],
)
def test_validator_blocks_everything_else(query):
    with pytest.raises(UnsafeQueryError):
        validator.validate(query)


def test_connection_url_is_parsed():
    target = Connection.parse("mysql://ro%40corp:p%3Ass@db.internal:3310/shop?ssl=true")

    assert target == Connection(
        host="db.internal",
        port=3310,
        user="ro@corp",
        password="p:ss",
        database="shop",
        ssl=True,
    )
    plain = Connection.parse("mysql://u@localhost")
    assert (plain.port, plain.database, plain.password, plain.ssl) == (
        3306,
        None,
        None,
        False,
    )


def test_mysql_is_registered():
    assert "mysql" in supported_schemes()
    assert datasource_kind("mysql://u@h/db") == "mysql"
    assert datasource_kind("mysql+pymysql://u@h/db") == "mysql"


def test_datasource_knows_its_dialect_and_default_schema():
    source = create_datasource("mysql://u:p@127.0.0.1:1/shop", Settings(), [])
    try:
        assert isinstance(source, MySQLDataSource)
        assert (source.sql_dialect, source.default_schema) == ("mysql", "shop")
        listed = MySQLDataSource("mysql://u@127.0.0.1:1", include_schemas=["a", "b"])
        assert listed.default_schema == "a"
        assert MySQLDataSource("mysql://u@127.0.0.1:1").default_schema == ""
    finally:
        source.close()


@pytest.mark.parametrize(
    ("column_type", "values"),
    [
        ("enum('a','b','c')", ["a", "b", "c"]),
        ("enum('it''s','plain')", ["it's", "plain"]),
        ("set('x','y')", ["x", "y"]),
        ("varchar(20)", []),
        ("int unsigned", []),
        (f"enum({','.join(repr(str(i)) for i in range(40))})", []),
    ],
)
def test_enum_values(column_type, values):
    assert enum_values(column_type) == values


def test_scope_limits_schemas_and_tables():
    where, params = scope(["shop"], None)
    assert where == "TABLE_SCHEMA IN (%s)" and params == ["shop"]

    where, params = scope([], [TableRef(schema_name="s", name="t")])
    assert "NOT IN" in where and "(TABLE_SCHEMA, TABLE_NAME) IN ((%s, %s))" in where
    assert params[-2:] == ["s", "t"]


TABLES = [
    {
        "schema_name": "shop",
        "table_name": "orders",
        "table_type": "BASE TABLE",
        "row_estimate": 20,
        "table_comment": "Customer orders",
    },
    {
        "schema_name": "shop",
        "table_name": "v",
        "table_type": "VIEW",
        "row_estimate": None,
        "table_comment": "VIEW",
    },
]
COLUMNS = [
    {
        "schema_name": "shop",
        "table_name": "orders",
        "column_name": "id",
        "column_type": "int",
        "is_nullable": "NO",
        "column_comment": "",
    },
    {
        "schema_name": "shop",
        "table_name": "orders",
        "column_name": "state",
        "column_type": "enum('a','b')",
        "is_nullable": "YES",
        "column_comment": "Order state",
    },
]
CONSTRAINTS = [
    {
        "schema_name": "shop",
        "table_name": "orders",
        "kind": "PRIMARY KEY",
        "column_name": "id",
        "ref_schema": None,
        "ref_table": None,
        "ref_column": None,
    },
    {
        "schema_name": "shop",
        "table_name": "orders",
        "kind": "FOREIGN KEY",
        "column_name": "customer_id",
        "ref_schema": "shop",
        "ref_table": "customers",
        "ref_column": "id",
    },
]


def test_schema_is_built_from_catalog_rows():
    schema = build_schema(TABLES, COLUMNS, CONSTRAINTS)

    orders = schema.get_table("shop.orders")
    assert orders.description == "Customer orders"
    assert orders.row_estimate == 20
    assert orders.primary_key == ["id"]
    assert orders.foreign_keys[0].references_table == "shop.customers"
    state = orders.columns[1]
    assert (state.nullable, state.description) == (True, "Order state")
    assert state.sample_values == ["a", "b"]
    assert orders.columns[0].description is None
    view = schema.get_table("shop.v")
    assert (view.kind, view.description) == (TableKind.VIEW, None)


def test_fingerprint_changes_with_columns_comments_and_keys():
    def fingerprint(tables=TABLES, columns=COLUMNS, constraints=CONSTRAINTS):
        return {
            f.table.name: f.fingerprint
            for f in build_fingerprints(tables, columns, constraints)
        }

    base = fingerprint()
    assert base == fingerprint()
    assert fingerprint(columns=COLUMNS[:1])["orders"] != base["orders"]
    assert fingerprint(constraints=CONSTRAINTS[:1])["orders"] != base["orders"]
    commented = [{**TABLES[0], "table_comment": "Other"}, TABLES[1]]
    assert fingerprint(tables=commented)["orders"] != base["orders"]
    assert fingerprint(columns=COLUMNS[:1])["v"] == base["v"]


def test_sampling_is_bounded_on_big_tables():
    table = TableRef(schema_name="shop", name="big`one")

    assert identifier(table) == "`shop`.`big``one`"
    small, params = sample_rows_sql(table, TableKind.TABLE, 100, 10)
    assert "ORDER BY RAND()" in small and params == [10]
    big, params = sample_rows_sql(table, TableKind.TABLE, 10_000_000, 10)
    assert "SELECT * FROM (SELECT * FROM" in big and params == [200, 10]
    view, params = sample_rows_sql(table, TableKind.VIEW, None, 10)
    assert "RAND" not in view and params == [10]


@pytest.mark.parametrize(
    ("data_type", "family"),
    [
        ("int", "integer"),
        ("bigint unsigned", "integer"),
        ("tinyint(1)", "integer"),
        ("varchar(255)", "text"),
        ("longtext", "text"),
        ("enum('a','b')", "text"),
        ("decimal(10,2)", "decimal"),
        ("double", "decimal"),
        ("datetime", "temporal"),
        ("json", "json"),
        ("blob", "other"),
        ("integer", "integer"),
        ("character varying", "text"),
    ],
)
def test_profiler_understands_mysql_types(data_type, family):
    assert type_family(data_type) == family
