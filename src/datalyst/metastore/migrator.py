from dataclasses import dataclass
from importlib import resources

from psycopg_pool import ConnectionPool

MIGRATIONS_PACKAGE = "datalyst.metastore.migrations"
MIGRATION_LOCK_KEY = 4_211_978_301

_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str


def discover_migrations() -> list[Migration]:
    files = resources.files(MIGRATIONS_PACKAGE).iterdir()
    found = [
        Migration(entry.name.removesuffix(".sql"), entry.read_text())
        for entry in files
        if entry.name.endswith(".sql")
    ]
    return sorted(found, key=lambda migration: migration.version)


def migrate(
    pool: ConnectionPool, migrations: list[Migration] | None = None
) -> list[str]:
    migrations = discover_migrations() if migrations is None else migrations
    applied: list[str] = []
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
        conn.execute(_CREATE_LEDGER)
        done = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }
        for migration in migrations:
            if migration.version in done:
                continue
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (migration.version,),
            )
            applied.append(migration.version)
    return applied


def pending_migrations(pool: ConnectionPool) -> list[str]:
    with pool.connection() as conn:
        exists = conn.execute(
            "SELECT to_regclass('schema_migrations') IS NOT NULL AS present"
        ).fetchone()["present"]
        done = (
            {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            if exists
            else set()
        )
    return [m.version for m in discover_migrations() if m.version not in done]
