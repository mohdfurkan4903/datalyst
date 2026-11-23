from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from datalyst.catalog.search import BM25TableSearch, TableSearch
from datalyst.core.models import DataSchema, GlossaryTerm, ValueHint, VerifiedQuery
from datalyst.enrichment.relationships import is_noise_table

IndexFactory = Callable[[DataSchema], TableSearch]


def _named_in(table_name: str, question: str) -> bool:
    return table_name.lower() in question.lower()


FUSION_K = 60


@dataclass
class Signals:
    rankings: list[list[str]] = field(default_factory=list)
    value_hints: list[ValueHint] = field(default_factory=list)
    glossary: list[GlossaryTerm] = field(default_factory=list)
    examples: list[VerifiedQuery] = field(default_factory=list)


@dataclass
class Candidates:
    tables: list[str]
    scores: dict[str, float] = field(default_factory=dict)
    value_hints: list[ValueHint] = field(default_factory=list)
    glossary: list[GlossaryTerm] = field(default_factory=list)
    examples: list[VerifiedQuery] = field(default_factory=list)
    searched: bool = True


def fuse(
    rankings: Sequence[Sequence[str]], k: int = FUSION_K
) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, name in enumerate(dict.fromkeys(ranking)):
            scores[name] += 1 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class ForeignKeyGraph:
    def __init__(self, schema: DataSchema) -> None:
        self._parents: dict[str, list[str]] = defaultdict(list)
        self._neighbours: dict[str, set[str]] = defaultdict(set)
        names = {table.qualified_name for table in schema.tables}
        for table in schema.tables:
            for fk in table.foreign_keys:
                if fk.references_table not in names:
                    continue
                self._parents[table.qualified_name].append(fk.references_table)
                self._neighbours[table.qualified_name].add(fk.references_table)
                self._neighbours[fk.references_table].add(table.qualified_name)

    def parents(self, name: str) -> list[str]:
        return list(dict.fromkeys(self._parents.get(name, [])))

    def path_to_any(self, start: str, targets: set[str], max_hops: int) -> list[str]:
        if not targets or start in targets:
            return []
        previous: dict[str, str | None] = {start: None}
        queue = deque([(start, 0)])
        while queue:
            node, hops = queue.popleft()
            if hops == max_hops:
                continue
            for neighbour in sorted(self._neighbours.get(node, ())):
                if neighbour in previous:
                    continue
                previous[neighbour] = node
                if neighbour in targets:
                    return _between(previous, neighbour)
                queue.append((neighbour, hops + 1))
        return []


def _between(previous: dict[str, str | None], end: str) -> list[str]:
    path = []
    node = previous[end]
    while node is not None and previous[node] is not None:
        path.append(node)
        node = previous[node]
    return path


class SchemaRetriever:
    def __init__(
        self,
        *,
        search_above_tables: int = 30,
        top_k: int = 10,
        max_tables: int = 20,
        candidate_limit: int = 30,
        max_join_hops: int = 3,
        min_relative_score: float = 0.2,
        index_factory: IndexFactory = BM25TableSearch,
    ) -> None:
        self._search_above_tables = search_above_tables
        self._top_k = top_k
        self._max_tables = max_tables
        self._candidate_limit = candidate_limit
        self._max_join_hops = max_join_hops
        self._min_relative_score = min_relative_score
        self._index_factory = index_factory
        self._cache: tuple[DataSchema, TableSearch, ForeignKeyGraph] | None = None

    @property
    def top_k(self) -> int:
        return self._top_k

    def needs_search(self, schema: DataSchema) -> bool:
        return len(schema.tables) > self._search_above_tables

    def candidates(
        self, schema: DataSchema, question: str, terms: Sequence[str] = ()
    ) -> Candidates:
        signals = self._visible(self._signals(schema, question, terms), schema)
        if not self.needs_search(schema):
            return Candidates(
                tables=[t.qualified_name for t in schema.tables],
                value_hints=signals.value_hints,
                glossary=signals.glossary,
                examples=signals.examples,
                searched=False,
            )
        known = {
            t.qualified_name
            for t in schema.tables
            if not is_noise_table(t) or _named_in(t.name, question)
        }
        rankings = [
            self._keyword_ranking(schema, [question, *terms]),
            *signals.rankings,
            [hint.table for hint in signals.value_hints],
            [name for example in signals.examples for name in example.tables],
        ]
        filtered = [[n for n in r if n in known] for r in rankings]
        fused = fuse([r for r in filtered if r])[: self._candidate_limit]
        return Candidates(
            tables=[name for name, _ in fused],
            scores={name: round(score, 5) for name, score in fused},
            value_hints=signals.value_hints,
            glossary=signals.glossary,
            examples=signals.examples,
        )

    def assemble(self, schema: DataSchema, tables: Sequence[str]) -> DataSchema:
        by_name = {table.qualified_name: table for table in schema.tables}
        seeds = [name for name in dict.fromkeys(tables) if name in by_name]
        if not seeds:
            return DataSchema()
        _, graph = self._indexes(schema)
        return DataSchema(
            tables=[by_name[name] for name in self._connect(seeds, graph)]
        )

    def retrieve(
        self, schema: DataSchema, terms: Sequence[str], force: bool = False
    ) -> DataSchema:
        if not force and not self.needs_search(schema):
            return schema
        question = " ".join(terms)
        ranked = self._keyword_ranking(schema, [question])
        return self.assemble(schema, ranked[: self._top_k])

    def _signals(
        self, schema: DataSchema, question: str, terms: Sequence[str]
    ) -> Signals:
        return Signals()

    def _visible(self, signals: Signals, schema: DataSchema) -> Signals:
        names = {t.qualified_name for t in schema.tables}
        return Signals(
            rankings=signals.rankings,
            value_hints=[h for h in signals.value_hints if h.table in names],
            glossary=signals.glossary,
            examples=[e for e in signals.examples if set(e.tables) <= names],
        )

    def _keyword_ranking(self, schema: DataSchema, terms: Sequence[str]) -> list[str]:
        index, _ = self._indexes(schema)
        hits = index.search(terms, self._candidate_limit)
        if not hits:
            return []
        cutoff = hits[0].score * self._min_relative_score
        return [hit.name for hit in hits if hit.score >= cutoff]

    def _connect(self, seeds: list[str], graph: ForeignKeyGraph) -> list[str]:
        selected: dict[str, None] = {}

        def add(*names: str) -> None:
            for name in names:
                if len(selected) < self._max_tables:
                    selected.setdefault(name)

        for seed in seeds:
            if seed in selected:
                continue
            bridge = graph.path_to_any(seed, set(selected), self._max_join_hops)
            add(seed, *bridge)
        for name in list(selected):
            add(*graph.parents(name))
        return list(selected)

    def _indexes(self, schema: DataSchema) -> tuple[TableSearch, ForeignKeyGraph]:
        cached = self._cache
        if cached is None or cached[0] is not schema:
            cached = (schema, self._index_factory(schema), ForeignKeyGraph(schema))
            self._cache = cached
        return cached[1], cached[2]
