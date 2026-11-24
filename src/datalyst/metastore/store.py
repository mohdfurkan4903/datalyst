from typing import Self

from psycopg_pool import ConnectionPool

from datalyst.core.exceptions import MetastoreError
from datalyst.metastore.migrator import migrate, pending_migrations
from datalyst.metastore.pool import create_pool
from datalyst.metastore.repositories import (
    CatalogRepository,
    EmbeddingRepository,
    HistoryRepository,
    JobRepository,
    KnowledgeRepository,
    SettingsRepository,
    SourceRepository,
    UsageRepository,
    UserRepository,
)


class Metastore:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool
        self.sources = SourceRepository(pool)
        self.catalog = CatalogRepository(pool)
        self.jobs = JobRepository(pool)
        self.embeddings = EmbeddingRepository(pool)
        self.usage = UsageRepository(pool)
        self.knowledge = KnowledgeRepository(pool)
        self.users = UserRepository(pool)
        self.history = HistoryRepository(pool)
        self.settings = SettingsRepository(pool)

    @classmethod
    def connect(
        cls,
        url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> Self:
        return cls(create_pool(url, min_size=min_size, max_size=max_size))

    def signing_key(self) -> str:
        """The random key, created by the first migration, that signs sign-ins."""
        with self.pool.connection() as conn:
            row = conn.execute("SELECT signing_key FROM instance").fetchone()
        if row is None:
            raise MetastoreError("The metastore is not set up; run: datalyst migrate")
        return row["signing_key"]

    def migrate(self) -> list[str]:
        return migrate(self.pool)

    def pending_migrations(self) -> list[str]:
        return pending_migrations(self.pool)

    def close(self) -> None:
        self.pool.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
