import pytest

from datalyst.core.exceptions import QueryExecutionError, UnsafeQueryError

pytestmark = pytest.mark.integration


def test_reads_schema_with_keys(datasource):
    schema = datasource.get_schema()

    assert {t.qualified_name for t in schema.tables} >= {
        "public.customers",
        "public.products",
        "public.orders",
        "public.order_items",
    }
    orders = schema.get_table("public.orders")
    assert orders.primary_key == ["id"]
    assert any(
        fk.column == "customer_id" and fk.references_table == "public.customers"
        for fk in orders.foreign_keys
    )


def test_executes_select(datasource):
    result = datasource.execute("SELECT count(*) AS total FROM public.orders")

    assert result.columns == ["total"]
    assert result.rows == [[2000]]
    assert result.row_count == 1
    assert result.truncated is False


def test_truncates_to_max_rows(datasource):
    result = datasource.execute("SELECT id FROM public.orders")

    assert result.row_count == 50
    assert len(result.rows) == 50
    assert result.truncated is True


def test_statement_timeout(datasource):
    with pytest.raises(QueryExecutionError, match="statement timeout"):
        datasource.execute("SELECT pg_sleep(5)")


def test_read_only_transaction_blocks_writes_even_without_validator(datasource):
    with pytest.raises(QueryExecutionError, match="read-only"):
        datasource.execute("CREATE TEMP TABLE scratch (id int)")


def test_validate_then_execute(datasource):
    query = datasource.validate(
        "SELECT name FROM public.customers ORDER BY id LIMIT 3;"
    )
    assert datasource.execute(query).row_count == 3

    with pytest.raises(UnsafeQueryError):
        datasource.validate("DELETE FROM public.orders")


def test_bad_query_raises_execution_error(datasource):
    with pytest.raises(QueryExecutionError, match="does not exist"):
        datasource.execute("SELECT missing_column FROM public.orders")


def test_pool_recovers_after_failed_query(datasource):
    with pytest.raises(QueryExecutionError):
        datasource.execute("SELECT 1/0")
    assert datasource.execute("SELECT 1").rows == [[1]]
