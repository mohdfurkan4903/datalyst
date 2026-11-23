from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol, Self, runtime_checkable

from datalyst.core.models import (
    ColumnRef,
    ColumnStats,
    Containment,
    DataSchema,
    QueryResult,
    Table,
    TableFingerprint,
    TableRef,
    ValueCount,
)


class QueryValidator(Protocol):
    def validate(self, query: str) -> str: ...


class DataSource(ABC):
    dialect: str
    query_language: str

    @abstractmethod
    def get_schema(self) -> DataSchema: ...

    @abstractmethod
    def validate(self, query: str) -> str: ...

    @abstractmethod
    def execute(self, query: str) -> QueryResult: ...

    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@runtime_checkable
class CatalogIntrospector(Protocol):
    def fingerprints(self) -> list[TableFingerprint]: ...

    def describe_tables(
        self, tables: Sequence[TableRef] | None = None
    ) -> list[Table]: ...

    def column_stats(self, table: TableRef) -> list[ColumnStats]: ...

    def sample_rows(self, table: TableRef, limit: int = 100) -> QueryResult: ...

    def top_values(
        self, column: ColumnRef, limit: int = 50, scan_rows: int = 10_000
    ) -> list[ValueCount]: ...

    def containment(
        self, child: ColumnRef, parent: ColumnRef, sample_size: int = 1000
    ) -> Containment: ...
