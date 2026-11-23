from enum import StrEnum
from typing import Any, TypedDict

from langchain_core.messages import BaseMessage

from datalyst.core.models import DataSchema, QueryResult


class ErrorKind(StrEnum):
    REFUSED = "refused"
    UNSAFE = "unsafe"
    SCHEMA = "schema"
    EXECUTION = "execution"


class AgentState(TypedDict, total=False):
    question: str
    catalog: DataSchema
    rewritten_question: str | None
    search_terms: list[str]
    candidates: Any
    selecting: bool
    selected: list[str]
    tables: list[str]
    schema: str
    context: str
    messages: list[BaseMessage]
    query: str | None
    result: QueryResult | None
    error: str | None
    error_kind: ErrorKind | None
    attempts: int
    healed: bool
    heal_schema: str | None
