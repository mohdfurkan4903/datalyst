from typing import Self

from psycopg_pool import ConnectionPool

from datalyst.metastore.migrator import migrate, pending_migrations
from datalyst.metastore.pool import create_pool
from datalyst.metastore.repositories import (
    CatalogRepository,
    EmbeddingRepository,
    GroupRepository,
    HistoryRepository,
    JobRepository,
    KnowledgeRepository,
    SourceRepository,
    UsageRepository,
    UserRepository,
)
from datalyst.security import SecretBox


class Metastore:
    def __init__(self, pool: ConnectionPool, secrets: SecretBox | None = None) -> None:
        self.pool = pool
        self.sources = SourceRepository(pool, secrets)
        self.catalog = CatalogRepository(pool)
        self.jobs = JobRepository(pool)
        self.embeddings = EmbeddingRepository(pool)
        self.usage = UsageRepository(pool)
        self.knowledge = KnowledgeRepository(pool)
        self.users = UserRepository(pool)
        self.groups = GroupRepository(pool)
        self.history = HistoryRepository(pool)

    @classmethod
    def connect(
        cls,
        url: str,
        secrets: SecretBox | None = None,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> Self:
        return cls(create_pool(url, min_size=min_size, max_size=max_size), secrets)

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
