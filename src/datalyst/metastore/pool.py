from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from datalyst.core.exceptions import MetastoreError


def create_pool(
    url: str, *, min_size: int = 1, max_size: int = 10, timeout: float = 10
) -> ConnectionPool:
    pool = ConnectionPool(
        url,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        kwargs={"row_factory": dict_row},
        open=True,
    )
    try:
        pool.wait(timeout=timeout)
    except PoolTimeout as e:
        pool.close()
        raise MetastoreError(f"Cannot connect to the metastore: {e}") from e
    return pool
