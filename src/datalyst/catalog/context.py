import itertools
import logging
import re
from collections.abc import Sequence

from langchain_core.embeddings import Embeddings

from datalyst.catalog.retriever import SchemaRetriever, Signals
from datalyst.catalog.search import tokenize
from datalyst.catalog.sqlrefs import tables_in_sql
from datalyst.core.models import DataSchema, GlossaryTerm, ValueHint, VerifiedQuery
from datalyst.enrichment.embeddings import vector_literal
from datalyst.metastore.repositories import EmbeddingRepository, KnowledgeRepository

logger = logging.getLogger(__name__)

VALUE_WORD = re.compile(r"[A-Za-z0-9][\w&'-]{2,}")
MAX_VALUE_TERMS = 24
MAX_VALUE_HINTS = 8
MAX_EXAMPLES = 3
MIN_EXAMPLE_SIMILARITY = 0.6
MIN_EXAMPLE_OVERLAP = 0.3


def value_terms(question: str, terms: Sequence[str]) -> list[str]:
    words = [w for w in VALUE_WORD.findall(question) if tokenize(w)]
    phrases = [f"{a} {b}" for a, b in itertools.pairwise(words)]
    extra = [t.strip() for t in terms if 3 <= len(t.strip()) <= 40]
    return list(dict.fromkeys([*words, *phrases, *extra]))[:MAX_VALUE_TERMS]


def matching_terms(glossary: list[GlossaryTerm], question: str) -> list[GlossaryTerm]:
    lowered = question.lower()
    words = set(tokenize(question))
    return [
        term
        for term in glossary
        if term.term.lower() in lowered
        or (tokens := set(tokenize(term.term)))
        and tokens <= words
    ]


def similar_by_words(
    examples: list[VerifiedQuery], question: str, limit: int = MAX_EXAMPLES
) -> list[VerifiedQuery]:
    words = set(tokenize(question))
    scored = []
    for example in examples:
        other = set(tokenize(example.question))
        if words and other:
            overlap = len(words & other) / len(words | other)
            if overlap >= MIN_EXAMPLE_OVERLAP:
                scored.append((overlap, example))
    scored.sort(key=lambda item: -item[0])
    return [example for _, example in scored[:limit]]


class MetastoreRetriever(SchemaRetriever):
    def __init__(
        self,
        source_id: int,
        knowledge: KnowledgeRepository,
        embedding_store: EmbeddingRepository,
        embeddings: Embeddings | None = None,
        embedding_model: str | None = None,
        **options: int,
    ) -> None:
        super().__init__(**options)
        self._source_id = source_id
        self._knowledge_store = knowledge
        self._embedding_store = embedding_store
        self._embeddings = embeddings
        self._embedding_model = embedding_model

    def _signals(
        self, schema: DataSchema, question: str, terms: Sequence[str]
    ) -> Signals:
        vector = self._embed(" ".join([question, *terms]))
        signals = Signals(
            value_hints=self._value_hints(question, terms),
            glossary=matching_terms(
                self._knowledge_store.glossary(self._source_id), question
            ),
            examples=self._examples(question, vector),
        )
        for term in signals.glossary:
            if term.sql_hint:
                signals.rankings.append(tables_in_sql(term.sql_hint, schema))
        if vector is not None and self.needs_search(schema):
            model = self._embedding_model or ""
            signals.rankings.append(
                [
                    name
                    for name, _ in self._embedding_store.search_tables(
                        self._source_id, model, vector, self._candidate_limit
                    )
                ]
            )
            signals.rankings.append(
                [
                    name.rsplit(".", 1)[0]
                    for name, _ in self._embedding_store.search_columns(
                        self._source_id, model, vector, self._candidate_limit * 2
                    )
                ]
            )
        return signals

    def _value_hints(self, question: str, terms: Sequence[str]) -> list[ValueHint]:
        hints = self._knowledge_store.value_matches(
            self._source_id, value_terms(question, terms)
        )
        unique: dict[tuple[str, str, str], ValueHint] = {}
        for hint in hints:
            unique.setdefault((hint.table, hint.column, hint.value), hint)
        return list(unique.values())[:MAX_VALUE_HINTS]

    def _examples(self, question: str, vector: str | None) -> list[VerifiedQuery]:
        if vector is not None and self._embedding_model:
            hits = self._embedding_store.search(
                self._source_id, self._embedding_model, vector, "query", MAX_EXAMPLES
            )
            ids = [
                object_id
                for object_id, score in hits
                if score >= MIN_EXAMPLE_SIMILARITY
            ]
            if ids:
                return self._knowledge_store.examples(self._source_id, ids)
        return similar_by_words(
            self._knowledge_store.examples(self._source_id), question
        )

    def _embed(self, text: str) -> str | None:
        if self._embeddings is None:
            return None
        try:
            return vector_literal(self._embeddings.embed_query(text))
        except Exception:
            logger.warning(
                "Question embedding failed; using keyword search", exc_info=True
            )
            return None
