import logging
from datetime import timedelta

from langchain_core.embeddings import Embeddings

from datalyst.agent import AnalystAgent
from datalyst.catalog import MetastoreRetriever, SchemaCatalog, SchemaRetriever
from datalyst.config import Settings
from datalyst.connectors import create_datasource
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.interfaces import CatalogIntrospector, DataSource
from datalyst.core.models import DataSchema, DataSourceInfo, TableRef
from datalyst.llm import create_chat_model, create_embeddings, embedding_model_name
from datalyst.metastore import Metastore
from datalyst.security import SecretBox, TokenService
from datalyst.services import AnalystRegistry, AnalystService, AuthService
from datalyst.sync import (
    DataSourceProvider,
    JobContext,
    SourceSynchronizer,
    SyncWorker,
    profile_table_request,
)

logger = logging.getLogger(__name__)


def open_metastore(settings: Settings) -> Metastore:
    secrets = (
        SecretBox(settings.secret_key.get_secret_value())
        if settings.secret_key
        else None
    )
    return Metastore.connect(
        settings.metastore_url,
        secrets,
        min_size=settings.metastore_pool_min_size,
        max_size=settings.metastore_pool_max_size,
    )


def open_datasource(
    settings: Settings, metastore: Metastore, source: DataSourceInfo
) -> DataSource:
    return create_datasource(
        metastore.sources.connection_url(source.id), settings, source.include_schemas
    )


def build_synchronizer(
    settings: Settings,
    metastore: Metastore,
    source: DataSourceInfo,
    datasource: DataSource,
) -> SourceSynchronizer:
    if not isinstance(datasource, CatalogIntrospector):
        raise ConfigurationError(f"The {source.kind} connector cannot be synced.")
    return SourceSynchronizer(
        metastore.catalog,
        metastore.sources,
        source.id,
        datasource,
        settings.sync_batch_size,
    )


def build_catalog(
    settings: Settings,
    metastore: Metastore,
    source: DataSourceInfo,
    datasource: DataSource,
) -> SchemaCatalog:
    return SchemaCatalog(
        metastore.catalog,
        source.id,
        build_synchronizer(settings, metastore, source, datasource),
    )


def build_worker(settings: Settings, metastore: Metastore) -> SyncWorker:
    context = JobContext(
        metastore,
        DataSourceProvider(settings, metastore),
        batch_size=settings.sync_batch_size,
        profile_sample_rows=settings.profile_sample_rows,
        profile_max_age=timedelta(hours=settings.profile_max_age_hours),
        llm=_worker_llm(settings.describe_settings()),
        llm_model=_model_label(settings.describe_settings()),
        describe_sample_rows=settings.describe_sample_rows,
        embeddings=_worker_embeddings(settings),
        embedding_model=embedding_model_name(settings),
        embed_columns=settings.embed_columns,
    )
    return SyncWorker(
        context,
        poll_interval=settings.worker_poll_interval_seconds,
        lease=timedelta(seconds=settings.worker_lease_seconds),
    )


def build_retriever(
    settings: Settings,
    metastore: Metastore | None = None,
    source: DataSourceInfo | None = None,
    embeddings: Embeddings | None = None,
) -> SchemaRetriever:
    options = {
        "search_above_tables": settings.retrieval_search_above_tables,
        "top_k": settings.retrieval_top_k,
        "max_tables": settings.retrieval_max_tables,
        "candidate_limit": settings.retrieval_candidates,
    }
    if metastore is None or source is None:
        return SchemaRetriever(**options)
    return MetastoreRetriever(
        source.id,
        metastore.knowledge,
        metastore.embeddings,
        embeddings,
        embedding_model_name(settings),
        **options,
    )


def build_service(
    settings: Settings,
    metastore: Metastore,
    source: str | DataSourceInfo | None = None,
) -> AnalystService:
    llm = create_chat_model(settings)
    if not isinstance(source, DataSourceInfo):
        source = metastore.sources.resolve(source)
    datasource = open_datasource(settings, metastore, source)
    synchronizer = build_synchronizer(settings, metastore, source, datasource)
    catalog = SchemaCatalog(metastore.catalog, source.id, synchronizer)

    def heal(_: list[str]) -> DataSchema:
        summary = synchronizer.sync_now()
        metastore.jobs.enqueue_many(
            profile_table_request(source.id, TableRef.parse(name))
            for name in [*summary.added, *summary.altered]
        )
        return catalog.get()

    agent = AnalystAgent(
        llm,
        datasource,
        retriever=build_retriever(
            settings, metastore, source, _worker_embeddings(settings)
        ),
        max_attempts=settings.agent_max_attempts,
        rewrite_question=settings.retrieval_rewrite_question,
        select_above=settings.retrieval_select_above,
        healer=heal,
    )
    return AnalystService(datasource, catalog, agent)


def _worker_llm(settings: Settings):
    if not settings.describe_enabled:
        return None
    try:
        return create_chat_model(settings)
    except ConfigurationError as e:
        logger.warning("Descriptions are off: %s", e)
        return None


def _worker_embeddings(settings: Settings):
    try:
        return create_embeddings(settings)
    except ConfigurationError as e:
        logger.warning("Embeddings are off: %s", e)
        return None


def _model_label(settings: Settings) -> str:
    return f"{settings.llm_provider}:{settings.llm_model}"


def build_tokens(settings: Settings) -> TokenService:
    if settings.secret_key is None:
        raise ConfigurationError(
            "Set DATALYST_SECRET_KEY before running the API. "
            "Create one with: datalyst secret-key"
        )
    return TokenService(
        settings.secret_key.get_secret_value(),
        timedelta(minutes=settings.auth_token_minutes),
    )


def build_auth(settings: Settings, metastore: Metastore) -> AuthService:
    return AuthService(metastore, build_tokens(settings))


def build_registry(settings: Settings, metastore: Metastore) -> AnalystRegistry:
    return AnalystRegistry(
        metastore, lambda source: build_service(settings, metastore, source)
    )
