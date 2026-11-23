import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from datalyst.connectors.postgres import PostgresDataSource
from datalyst.core.exceptions import QueryExecutionError
from datalyst.metastore import Metastore
from datalyst.security import SecretBox, generate_secret_key

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


DEFAULT_METASTORE_URL = "postgresql://datalyst:datalyst@localhost:5434/datalyst"


@pytest.fixture(scope="session")
def metastore_url():
    admin_url = os.getenv("DATALYST_TEST_METASTORE_URL", DEFAULT_METASTORE_URL)
    name = f"datalyst_test_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=2) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.Error as e:
        pytest.skip(
            f"Metastore not reachable at {admin_url} ({e}). Run: docker compose up -d"
        )
    yield conninfo_to_url(admin_url, name)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(name)
            )
        )


@pytest.fixture
def metastore(metastore_url):
    with Metastore.connect(metastore_url, SecretBox(generate_secret_key())) as store:
        store.migrate()
        with store.pool.connection() as conn:
            conn.execute(
                "TRUNCATE data_sources, sync_workers, users, groups, llm_usage RESTART IDENTITY CASCADE"
            )
        yield store


def conninfo_to_url(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


DEFAULT_SHOP_ADMIN_URL = (
    "postgresql://datalyst_admin:datalyst_admin@localhost:5433/shop"
)
DEFAULT_WAREHOUSE_URL = "postgresql://wh_ro:wh_ro@localhost:5435/erp"


@pytest.fixture
def shop_admin(database_url):
    url = os.getenv("DATALYST_TEST_SHOP_ADMIN_URL", DEFAULT_SHOP_ADMIN_URL)
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def warehouse_url() -> str:
    url = os.getenv("DATALYST_TEST_WAREHOUSE_URL", DEFAULT_WAREHOUSE_URL)
    try:
        with PostgresDataSource(url, pool_min_size=0, connect_timeout_seconds=2) as ds:
            ds.execute("SELECT 1")
    except (QueryExecutionError, psycopg.Error) as e:
        pytest.skip(
            f"Warehouse not reachable at {url} ({e}). Run: docker compose up -d"
        )
    return url


@pytest.fixture
def warehouse(warehouse_url):
    with PostgresDataSource(warehouse_url, timeout_seconds=10) as ds:
        yield ds
