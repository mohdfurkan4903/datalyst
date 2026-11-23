"""Integration test fixtures.

Everything runs on one Postgres (``docker compose up -d`` starts it). Each test
session creates throwaway databases on it: a metastore, and the ``shop`` and
``erp`` sample databases loaded from ``tests/fixtures``, each with a read-only
login like the one a real installation would use.
"""

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from datalyst.connectors.postgres import PostgresDataSource
from datalyst.metastore import Metastore
from datalyst.security import SecretBox, generate_secret_key

FIXTURES = Path(__file__).parents[1] / "fixtures"
DEFAULT_ADMIN_URL = "postgresql://datalyst:datalyst@localhost:5434/datalyst"
READ_ONLY_ROLE = "datalyst_test_ro"
READ_ONLY_PASSWORD = "datalyst_test_ro"


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = os.getenv("DATALYST_TEST_POSTGRES_URL", DEFAULT_ADMIN_URL)
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as e:
        pytest.skip(f"Postgres not reachable at {url} ({e}). Run: docker compose up -d")
    return url


def with_database(url: str, database: str, user: str | None = None) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc
    if user is not None:
        netloc = f"{user}:{READ_ONLY_PASSWORD}@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit(parts._replace(path=f"/{database}", netloc=netloc))


@pytest.fixture(scope="session")
def temporary_database(admin_url):
    created = []

    def create(prefix: str, fixture: str | None = None) -> str:
        name = f"datalyst_test_{prefix}_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created.append(name)
        url = with_database(admin_url, name)
        if fixture:
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute((FIXTURES / fixture).read_text())
                _grant_read_only(conn, name)
        return url

    yield create
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for name in created:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )


def _grant_read_only(conn: psycopg.Connection, database: str) -> None:
    conn.execute(
        sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {role}) "
            "THEN CREATE ROLE {ident} LOGIN PASSWORD {password}; END IF; END $$"
        ).format(
            role=sql.Literal(READ_ONLY_ROLE),
            ident=sql.Identifier(READ_ONLY_ROLE),
            password=sql.Literal(READ_ONLY_PASSWORD),
        )
    )
    role = sql.Identifier(READ_ONLY_ROLE)
    for statement in (
        "GRANT CONNECT ON DATABASE {db} TO {role}",
        "GRANT USAGE ON SCHEMA public TO {role}",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}",
        "ALTER ROLE {role} SET default_transaction_read_only = on",
    ):
        conn.execute(sql.SQL(statement).format(db=sql.Identifier(database), role=role))
    conn.execute("ANALYZE")


@pytest.fixture(scope="session")
def shop_owner_url(temporary_database) -> str:
    return temporary_database("shop", "shop.sql")


@pytest.fixture(scope="session")
def database_url(shop_owner_url) -> str:
    """Read-only login to the shop sample database."""
    return with_database(
        shop_owner_url, urlsplit(shop_owner_url).path.lstrip("/"), READ_ONLY_ROLE
    )


@pytest.fixture
def datasource(database_url):
    with PostgresDataSource(database_url, timeout_seconds=2, max_rows=50) as ds:
        yield ds


@pytest.fixture
def shop_admin(shop_owner_url):
    with psycopg.connect(shop_owner_url, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def warehouse_url(temporary_database) -> str:
    """Read-only login to the messy ERP sample database."""
    owner = temporary_database("erp", "erp.sql")
    return with_database(owner, urlsplit(owner).path.lstrip("/"), READ_ONLY_ROLE)


@pytest.fixture
def warehouse(warehouse_url):
    with PostgresDataSource(warehouse_url, timeout_seconds=10) as ds:
        yield ds


@pytest.fixture(scope="session")
def metastore_url(temporary_database) -> str:
    return temporary_database("meta")


@pytest.fixture
def metastore(metastore_url):
    with Metastore.connect(metastore_url, SecretBox(generate_secret_key())) as store:
        store.migrate()
        with store.pool.connection() as conn:
            conn.execute(
                "TRUNCATE data_sources, sync_workers, users, groups, llm_usage, settings "
                "RESTART IDENTITY CASCADE"
            )
            conn.execute("UPDATE settings_version SET version = 0")
        yield store
