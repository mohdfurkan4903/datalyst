import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from datalyst.core.models import DataSchema, Table

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "get",
        "give",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "list",
        "me",
        "my",
        "of",
        "on",
        "or",
        "per",
        "show",
        "than",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
        "most",
        "least",
        "top",
        "whose",
        "many",
        "much",
        "every",
        "each",
        "please",
        "all",
    }
)

TABLE_NAME_WEIGHT = 3
COLUMN_NAME_WEIGHT = 2
SYNONYM_WEIGHT = 2


def tokenize(text: str) -> list[str]:
    words = _WORD.findall(_CAMEL_BOUNDARY.sub(r"\1 \2", text).lower())
    return [_stem(word) for word in words if word not in _STOPWORDS]


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


@dataclass(frozen=True)
class TableHit:
    name: str
    score: float


class TableSearch(Protocol):
    def search(self, terms: Sequence[str], limit: int) -> list[TableHit]: ...


class BM25TableSearch:
    def __init__(self, schema: DataSchema, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._names = [table.qualified_name for table in schema.tables]
        documents = [Counter(_table_tokens(table)) for table in schema.tables]
        self._lengths = [sum(doc.values()) for doc in documents]
        self._average_length = (sum(self._lengths) / len(documents)) if documents else 0
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for index, doc in enumerate(documents):
            for token, frequency in doc.items():
                self._postings[token].append((index, frequency))
        total = len(documents)
        self._idf = {
            token: math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for token, postings in self._postings.items()
        }

    def search(self, terms: Sequence[str], limit: int) -> list[TableHit]:
        scores: dict[int, float] = defaultdict(float)
        for token in set(tokenize(" ".join(terms))):
            idf = self._idf.get(token)
            if idf is None:
                continue
            for index, frequency in self._postings[token]:
                scores[index] += idf * self._saturate(frequency, self._lengths[index])
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [TableHit(self._names[i], round(s, 4)) for i, s in ranked[:limit]]

    def _saturate(self, frequency: int, length: int) -> float:
        norm = 1 - self._b + self._b * length / self._average_length
        return frequency * (self._k1 + 1) / (frequency + self._k1 * norm)


def _table_tokens(table: Table) -> Iterable[str]:
    yield from tokenize(table.name) * TABLE_NAME_WEIGHT
    yield from tokenize(table.schema_name)
    yield from tokenize(table.description or "")
    for synonym in table.synonyms:
        yield from tokenize(synonym) * SYNONYM_WEIGHT
    for question in table.example_questions:
        yield from tokenize(question)
    for column in table.columns:
        yield from tokenize(column.name) * COLUMN_NAME_WEIGHT
        yield from tokenize(column.description or "")
        for synonym in column.synonyms:
            yield from tokenize(synonym)
        for value in column.sample_values:
            yield from tokenize(value)
