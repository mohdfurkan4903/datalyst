from abc import ABC, abstractmethod
from typing import Protocol, Self

from datalyst.core.models import DataSchema, QueryResult


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
