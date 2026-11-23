import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from datalyst.core.models import (
    DataSchema,
    Job,
    JobRequest,
    LLMDataPolicy,
    RelationshipOrigin,
    TableRef,
)
from datalyst.enrichment import describer, pipeline
from datalyst.enrichment.embeddings import (
    QUERY,
    EmbeddingDocument,
    build_documents,
    vector_literal,
)
from datalyst.metastore import Metastore
from datalyst.sync.datasources import DataSourceProvider
from datalyst.sync.synchronizer import SourceSynchronizer

CHECK_SOURCE = "check_source"
SYNC_TABLES = "sync_tables"
PROFILE_TABLE = "profile_table"
INFER_RELATIONSHIPS = "infer_relationships"
DESCRIBE_SOURCE = "describe_source"
DESCRIBE_TABLE = "describe_table"
EMBED_SOURCE = "embed_source"

PRIORITY = {
    CHECK_SOURCE: 10,
    SYNC_TABLES: 20,
    PROFILE_TABLE: 50,
    INFER_RELATIONSHIPS: 60,
    DESCRIBE_SOURCE: 70,
    DESCRIBE_TABLE: 75,
    EMBED_SOURCE: 80,
}
EMBED_BATCH_SIZE = 32
EMBED_CONTINUE_AFTER = timedelta(seconds=65)

logger = logging.getLogger(__name__)


@dataclass
class JobContext:
    metastore: Metastore
    datasources: DataSourceProvider
    batch_size: int = 200
    profile_sample_rows: int = 500
    profile_max_age: timedelta = timedelta(hours=24)
    infer_delay: timedelta = timedelta(seconds=15)
    llm: BaseChatModel | None = None
    llm_model: str = ""
    describe_sample_rows: int = 5
    embeddings: Embeddings | None = None
    embedding_model: str | None = None
    embed_columns: bool = True
    embed_delay: timedelta = timedelta(seconds=10)

    def synchronizer(self, source_id: int) -> SourceSynchronizer:
        return SourceSynchronizer(
            self.metastore.catalog,
            self.metastore.sources,
            source_id,
            self.datasources.introspector(source_id),
            self.batch_size,
        )


Handler = Callable[[Job, JobContext], list[JobRequest]]


def check_source_request(source_id: int) -> JobRequest:
    return JobRequest(
        source_id=source_id, kind=CHECK_SOURCE, priority=PRIORITY[CHECK_SOURCE]
    )


def sync_tables_request(
    source_id: int, refs: list[TableRef], fingerprints: dict[TableRef, str]
) -> JobRequest:
    names = sorted(ref.qualified_name for ref in refs)
    digest = hashlib.sha1("\n".join(names).encode()).hexdigest()[:16]
    return JobRequest(
        source_id=source_id,
        kind=SYNC_TABLES,
        target=f"{len(names)}:{digest}",
        priority=PRIORITY[SYNC_TABLES],
        payload={
            "tables": [
                [ref.schema_name, ref.name, fingerprints.get(ref)] for ref in refs
            ]
        },
    )


def profile_table_request(source_id: int, ref: TableRef) -> JobRequest:
    return JobRequest(
        source_id=source_id,
        kind=PROFILE_TABLE,
        target=ref.qualified_name,
        priority=PRIORITY[PROFILE_TABLE],
    )


def infer_relationships_request(source_id: int, delay: timedelta) -> JobRequest:
    return JobRequest(
        source_id=source_id,
        kind=INFER_RELATIONSHIPS,
        priority=PRIORITY[INFER_RELATIONSHIPS],
        delay=delay,
    )


def describe_source_request(source_id: int) -> JobRequest:
    return JobRequest(
        source_id=source_id, kind=DESCRIBE_SOURCE, priority=PRIORITY[DESCRIBE_SOURCE]
    )


def describe_table_request(source_id: int, ref: TableRef) -> JobRequest:
    return JobRequest(
        source_id=source_id,
        kind=DESCRIBE_TABLE,
        target=ref.qualified_name,
        priority=PRIORITY[DESCRIBE_TABLE],
    )


def embed_source_request(source_id: int, delay: timedelta) -> JobRequest:
    return JobRequest(
        source_id=source_id,
        kind=EMBED_SOURCE,
        priority=PRIORITY[EMBED_SOURCE],
        delay=delay,
    )


def check_source(job: Job, context: JobContext) -> list[JobRequest]:
    result = context.synchronizer(job.source_id).check()
    syncing = set(result.diff.to_sync)
    stale = context.metastore.catalog.tables_to_profile(
        job.source_id, context.profile_max_age
    )
    return [
        *(
            sync_tables_request(job.source_id, batch, result.fingerprints)
            for batch in result.batches(context.batch_size)
        ),
        *(
            profile_table_request(job.source_id, ref)
            for ref in stale
            if ref not in syncing
        ),
    ]


def sync_tables(job: Job, context: JobContext) -> list[JobRequest]:
    entries = job.payload.get("tables", [])
    refs = [TableRef(schema_name=schema, name=name) for schema, name, _ in entries]
    fingerprints = {
        TableRef(schema_name=schema, name=name): fingerprint
        for schema, name, fingerprint in entries
        if fingerprint
    }
    summary = context.synchronizer(job.source_id).sync_tables(
        refs, fingerprints or None
    )
    changed = {*summary.added, *summary.altered}
    return [
        profile_table_request(job.source_id, ref)
        for ref in refs
        if ref.qualified_name in changed
    ]


def profile_table(job: Job, context: JobContext) -> list[JobRequest]:
    ref = TableRef.parse(job.target)
    catalog = context.metastore.catalog
    schema = catalog.load_schema(job.source_id, tables=[ref])
    if not schema.tables:
        return []
    result = pipeline.profile(
        context.datasources.introspector(job.source_id),
        schema.tables[0],
        context.profile_sample_rows,
    )
    catalog.save_profiles(
        job.source_id, ref, result.profiles, result.values, result.sample_values
    )
    return [infer_relationships_request(job.source_id, context.infer_delay)]


def infer_relationships(job: Job, context: JobContext) -> list[JobRequest]:
    catalog = context.metastore.catalog
    schema = catalog.load_schema(
        job.source_id,
        min_confidence=0,
        origins=[RelationshipOrigin.DECLARED, RelationshipOrigin.HUMAN],
    )
    found = pipeline.relationships(
        context.datasources.introspector(job.source_id), schema
    )
    catalog.replace_inferred_relationships(job.source_id, found)
    return [describe_source_request(job.source_id)]


def describe_source(job: Job, context: JobContext) -> list[JobRequest]:
    if context.llm is None:
        return [embed_source_request(job.source_id, timedelta(0))]
    catalog = context.metastore.catalog
    policy = context.metastore.sources.get(job.source_id).llm_data_policy
    schema = catalog.load_schema(job.source_id, ai_descriptions=False)
    incoming = _incoming_by_table(schema)
    state = catalog.description_state(job.source_id)
    needed = []
    for table in schema.tables:
        key = describer.description_key(
            table, incoming.get(table.qualified_name, []), policy, context.llm_model
        )
        current = state.get(table.ref)
        if (
            current is None
            or current["description_hash"] != key
            or current["incomplete"]
        ):
            needed.append(describe_table_request(job.source_id, table.ref))
    return needed or [embed_source_request(job.source_id, timedelta(0))]


def describe_table(job: Job, context: JobContext) -> list[JobRequest]:
    if context.llm is None:
        return []
    ref = TableRef.parse(job.target)
    catalog = context.metastore.catalog
    schema = catalog.load_schema(job.source_id, tables=[ref], ai_descriptions=False)
    if not schema.tables:
        return []
    table = schema.tables[0]
    policy = context.metastore.sources.get(job.source_id).llm_data_policy
    incoming = catalog.incoming_joins(job.source_id, ref)
    sample = None
    if policy == LLMDataPolicy.ROWS and context.describe_sample_rows:
        sample = context.datasources.introspector(job.source_id).sample_rows(
            ref, context.describe_sample_rows
        )
    description, usage = describer.describe(
        context.llm, table, incoming, policy, sample, context.llm_model
    )
    key = describer.description_key(table, incoming, policy, context.llm_model)
    catalog.save_description(job.source_id, ref, description, key)
    context.metastore.usage.record(job.source_id, usage)
    return [embed_source_request(job.source_id, context.embed_delay)]


def embed_source(job: Job, context: JobContext) -> list[JobRequest]:
    if context.embeddings is None or not context.embedding_model:
        return []
    metastore, model = context.metastore, context.embedding_model
    documents = build_documents(
        metastore.catalog.load_schema(job.source_id),
        metastore.catalog.object_ids(job.source_id),
        context.embed_columns,
    )
    documents += [
        EmbeddingDocument(QUERY, example.id, example.question)
        for example in metastore.knowledge.examples(job.source_id)
        if example.id is not None
    ]
    known = metastore.embeddings.hashes(job.source_id, model)
    pending = [
        d
        for d in documents
        if known.get((d.object_type, d.object_id)) != d.content_hash
    ]
    saved = 0
    for start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[start : start + EMBED_BATCH_SIZE]
        try:
            vectors = context.embeddings.embed_documents([d.text for d in batch])
        except Exception:
            if not saved:
                raise
            logger.warning(
                "Embedding paused after %d of %d documents; continuing later",
                saved,
                len(pending),
                exc_info=True,
            )
            return [embed_source_request(job.source_id, EMBED_CONTINUE_AFTER)]
        saved += metastore.embeddings.save(
            job.source_id,
            model,
            (
                (d.object_type, d.object_id, d.content_hash, vector_literal(v))
                for d, v in zip(batch, vectors, strict=True)
            ),
        )
    metastore.embeddings.keep_only(
        job.source_id, model, [(d.object_type, d.object_id) for d in documents]
    )
    return []


def _incoming_by_table(schema: DataSchema) -> dict[str, list[str]]:
    incoming: dict[str, list[str]] = {}
    for table in schema.tables:
        for fk in table.foreign_keys:
            incoming.setdefault(fk.references_table, []).append(
                f"{table.qualified_name}.{fk.column}"
            )
    return incoming


HANDLERS: dict[str, Handler] = {
    CHECK_SOURCE: check_source,
    SYNC_TABLES: sync_tables,
    PROFILE_TABLE: profile_table,
    INFER_RELATIONSHIPS: infer_relationships,
    DESCRIBE_SOURCE: describe_source,
    DESCRIBE_TABLE: describe_table,
    EMBED_SOURCE: embed_source,
}
