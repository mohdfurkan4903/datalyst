import pytest

from datalyst.connectors.postgres import PostgresQueryValidator
from datalyst.core.exceptions import UnsafeQueryError

validator = PostgresQueryValidator()


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "select * from public.orders limit 10",
        "SELECT * FROM orders;",
        "  SELECT count(*) FROM orders ;  ",
        (
            "SELECT c.name, count(o.id) FROM customers c "
            "JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY 2 DESC"
        ),
        (
            "WITH recent AS (SELECT * FROM orders WHERE ordered_at > now() - interval '30 days') "
            "SELECT count(*) FROM recent"
        ),
        "SELECT id FROM orders UNION SELECT id FROM customers",
        "SELECT id FROM orders INTERSECT SELECT customer_id FROM orders",
        "SELECT id FROM orders EXCEPT SELECT customer_id FROM orders",
        "(SELECT 1)",
        "SELECT * FROM (SELECT * FROM orders) AS sub",
        "SELECT id, rank() OVER (PARTITION BY customer_id ORDER BY ordered_at) FROM orders",
        "SELECT lower(name), date_trunc('month', created_at) FROM customers",
        "SELECT 'DELETE FROM orders' AS text",
        "SELECT 1 -- DROP TABLE orders",
        "SELECT /* ; DELETE FROM orders */ 1",
    ],
)
def test_allows_read_only_queries(query):
    assert validator.validate(query) == query.strip().rstrip(";").strip()


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO orders (id) VALUES (1)",
        "UPDATE orders SET status = 'paid'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "ALTER TABLE orders ADD COLUMN x int",
        "CREATE TABLE x (id int)",
        "MERGE INTO orders o USING customers c ON o.id = c.id WHEN MATCHED THEN DELETE",
        "GRANT SELECT ON orders TO public",
        "COPY orders TO '/tmp/orders.csv'",
        "SET statement_timeout = 0",
        "BEGIN",
        "COMMIT",
        "VACUUM",
        "SHOW search_path",
        "EXPLAIN ANALYZE DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
        "SELECT 1; SELECT 2",
        "SELECT * INTO backup FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
        "SELECT pg_sleep(10)",
        "SELECT PG_SLEEP(10)",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT pg_terminate_backend(123)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT nextval('orders_id_seq')",
        "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)",
        "",
        "   ;  ",
        "this is not sql",
    ],
)
def test_blocks_unsafe_queries(query):
    with pytest.raises(UnsafeQueryError):
        validator.validate(query)
