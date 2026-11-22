from enum import StrEnum
from typing import TypedDict

from langchain_core.messages import BaseMessage

from datalyst.core.models import QueryResult


class ErrorKind(StrEnum):
    REFUSED = "refused"
    UNSAFE = "unsafe"
    EXECUTION = "execution"


class AgentState(TypedDict, total=False):
    question: str
    schema: str
    messages: list[BaseMessage]
    query: str | None
    result: QueryResult | None
    error: str | None
    error_kind: ErrorKind | None
    attempts: int
