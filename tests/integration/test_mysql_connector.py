import pytest

from datalyst.connectors.mysql import MySQLDataSource
from datalyst.core.exceptions import QueryExecutionError, UnsafeQueryError
from datalyst.core.models import ColumnRef, TableKind, TableRef
from datalyst.enrichment.pipeline import profile, relationships

pytestmark = pytest.mark.integration


def ref(name: str, schema: str) -> TableRef:
    return TableRef(schema_name=schema, name=name)


def test_reads_schema_with_keys(mysql_source, mysql_shop):
    schema = mysql_source.get_schema()
    db = mysql_shop["database"]

    assert {t.qualified_name for t in schema.tables} == {
        f"{db}.customers",
        f"{db}.products",
        f"{db}.orders",
        f"{db}.order_items",
        f"{db}.visits",
        f"{db}.customer_orders",
    }
    orders = schema.get_table(f"{db}.orders")
    assert orders.primary_key == ["id"]
    assert any(
        fk.column == "customer_id" and fk.references_table == f"{db}.customers"
        for fk in orders.foreign_keys
    )
    assert schema.get_table(f"{db}.customer_orders").kind == TableKind.VIEW


def test_reads_comments_and_enum_values(mysql_source, mysql_shop):
    schema = mysql_source.get_schema()
    customers = schema.get_table(f"{mysql_shop['database']}.customers")
    products = schema.get_table(f"{mysql_shop['database']}.products")

    assert customers.description == "People who have bought something"
    country = next(c for c in customers.columns if c.name == "country")
    assert country.description == "ISO country code"
    category = next(c for c in products.columns if c.name == "category")
    assert category.sample_values == ["books", "toys", "garden", "kitchen"]


def test_view_has_no_comment(mysql_source, mysql_shop):
    view = mysql_source.get_schema().get_table(
        f"{mysql_shop['database']}.customer_orders"
    )

    assert view.description is None


def test_describes_only_the_tables_asked_for(mysql_source, mysql_shop):
    db = mysql_shop["database"]

    tables = mysql_source.describe_tables([ref("orders", db)])

    assert [t.qualified_name for t in tables] == [f"{db}.orders"]
    assert mysql_source.describe_tables([]) == []


def test_executes_select(mysql_source):
    result = mysql_source.execute("SELECT COUNT(*) AS total FROM orders")

    assert result.columns == ["total"]
    assert result.rows == [[2000]]
    assert result.truncated is False


def test_truncates_to_max_rows(mysql_source):
    result = mysql_source.execute("SELECT id FROM orders")

    assert result.row_count == 50
    assert result.truncated is True
    # The connection that streamed the big result is not reused in a broken state.
    assert mysql_source.execute("SELECT 1").rows == [[1]]


def test_query_timeout(mysql_source):
    with pytest.raises(QueryExecutionError, match="execution time"):
        mysql_source.execute(
            "SELECT SUM(a.id * b.id * c.id) FROM order_items a, order_items b, order_items c"
        )


def test_session_is_read_only_even_for_a_login_that_can_write(mysql_shop):
    with MySQLDataSource(mysql_shop["owner"]) as owner:
        with pytest.raises(QueryExecutionError, match="READ ONLY"):
            owner.execute("UPDATE products SET price = 0")
        assert owner.execute("SELECT MIN(price) FROM products").rows[0][0] > 0


def test_validator_blocks_unsafe_queries(mysql_source):
    for query in (
        "DELETE FROM orders",
        "SELECT SLEEP(5)",
        "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
        "SELECT * FROM orders FOR UPDATE",
    ):
        with pytest.raises(UnsafeQueryError):
            mysql_source.validate(query)
    assert (
        mysql_source.validate("SELECT `id` FROM `orders`;")
        == "SELECT `id` FROM `orders`"
    )


def test_reports_sql_errors(mysql_source):
    with pytest.raises(QueryExecutionError, match="doesn't exist"):
        mysql_source.execute("SELECT * FROM nope")


def test_check_reports_the_real_reason(mysql_shop):
    wrong_password = mysql_shop["read_only"].replace(":datalyst_test_ro@", ":wrong@")

    with (
        MySQLDataSource(wrong_password) as source,
        pytest.raises(QueryExecutionError, match="Access denied"),
    ):
        source.check()
    with MySQLDataSource(mysql_shop["read_only"]) as source:
        source.check()


def test_fingerprints_change_only_for_the_altered_table(mysql_source, mysql_shop):
    db = mysql_shop["database"]
    before = {f.table.name: f.fingerprint for f in mysql_source.fingerprints()}

    admin = mysql_shop["admin"].cursor()
    admin.execute(f"ALTER TABLE `{db}`.products ADD COLUMN sku VARCHAR(20)")
    try:
        after = {f.table.name: f.fingerprint for f in mysql_source.fingerprints()}
    finally:
        admin.execute(f"ALTER TABLE `{db}`.products DROP COLUMN sku")

    assert before.keys() == after.keys()
    assert [n for n in before if before[n] != after[n]] == ["products"]
    assert {f.table.name: f.fingerprint for f in mysql_source.fingerprints()} == before


def test_samples_rows(mysql_source, mysql_shop):
    sample = mysql_source.sample_rows(ref("orders", mysql_shop["database"]), 25)

    assert sample.row_count == 25
    assert sample.columns == ["id", "customer_id", "status", "ordered_at"]


def test_top_values_and_containment(mysql_source, mysql_shop):
    db = mysql_shop["database"]
    status = ColumnRef(table=ref("orders", db), column="status")
    values = mysql_source.top_values(status)

    assert {v.value for v in values} == {"pending", "paid", "shipped", "cancelled"}
    assert all(v.count == 500 for v in values)

    child = ColumnRef(table=ref("orders", db), column="customer_id")
    parent = ColumnRef(table=ref("customers", db), column="id")
    assert mysql_source.containment(child, parent).ratio == 1.0
    wrong = ColumnRef(table=ref("products", db), column="id")
    assert mysql_source.containment(child, wrong).ratio < 1.0


def test_enrichment_runs_on_mysql(mysql_source, mysql_shop):
    db = mysql_shop["database"]
    schema = mysql_source.get_schema()
    orders = schema.get_table(f"{db}.orders")

    result = profile(mysql_source, orders)

    assert result.profiles["status"].top_values
    assert result.profiles["id"].unique
    inferred = relationships(mysql_source, schema)
    assert isinstance(inferred, list)


def test_schema_without_database_in_url_lists_every_visible_database(mysql_shop):
    url = mysql_shop["read_only"].rsplit("/", 1)[0]
    with MySQLDataSource(url) as source:
        names = {t.qualified_name for t in source.get_schema().tables}

    assert f"{mysql_shop['database']}.orders" in names
    assert source.default_schema == ""
