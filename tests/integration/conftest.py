import os

import psycopg
import pytest

from datalyst.connectors.postgres import PostgresDataSource
from datalyst.core.exceptions import QueryExecutionError

DEFAULT_URL = "postgresql://datalyst_ro:datalyst_ro@localhost:5433/shop"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.getenv("DATALYST_TEST_DATABASE_URL", DEFAULT_URL)
    try:
        with PostgresDataSource(url, pool_min_size=0, connect_timeout_seconds=2) as ds:
            ds.execute("SELECT 1")
    except (QueryExecutionError, psycopg.Error) as e:
        pytest.skip(f"Postgres not reachable at {url} ({e}). Run: docker compose up -d")
    return url


@pytest.fixture
def datasource(database_url):
    with PostgresDataSource(database_url, timeout_seconds=2, max_rows=50) as ds:
        yield ds
