import logging
from datetime import timedelta

from langchain_core.embeddings import Embeddings

from datalyst.agent import AnalystAgent
from datalyst.catalog import MetastoreRetriever, SchemaCatalog, SchemaRetriever
from datalyst.config import BootSettings, Settings
from datalyst.connectors import create_datasource
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.interfaces import CatalogIntrospector, DataSource
from datalyst.core.models import DataSchema, DataSourceInfo, TableRef
from datalyst.llm import create_chat_model, create_embeddings, embedding_model_name
from datalyst.metastore import Metastore
from datalyst.security import TokenService
from datalyst.services import (
    AnalystRegistry,
    AnalystService,
    AuthService,
    ConfigService,
)
from datalyst.sync import (
    DataSourceProvider,
    JobContext,
    SourceSynchronizer,
    SyncWorker,
    WorkerSetup,
    profile_table_request,
)

logger = logging.getLogger(__name__)


def open_metastore(boot: BootSettings) -> Metastore:
    return Metastore.connect(
        boot.require_metastore_url(),
        min_size=boot.metastore_pool_min_size,
        max_size=boot.metastore_pool_max_size,
    )


def open_ready_metastore(boot: BootSettings) -> Metastore:
    """Open the metastore and fail clearly if migrations have not been applied."""
    metastore = open_metastore(boot)
    try:
        pending = metastore.pending_migrations()
    except Exception:
        metastore.close()
        raise
    if pending:
        metastore.close()
        raise ConfigurationError(
            f"Metastore needs migrations ({', '.join(pending)}). Run: datalyst migrate"
        )
    return metastore


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


def build_worker(metastore: Metastore, config: ConfigService) -> SyncWorker:
    """A sync worker that rebuilds its models and connections when settings change."""
    seen = config.version()

    def reconfigure() -> WorkerSetup | None:
        nonlocal seen
        version = config.version()
        if version == seen:
            return None
        seen = version
        return _worker_setup(config.current(), metastore)

    return SyncWorker(
        _worker_setup(config.current(), metastore), reconfigure=reconfigure
    )


def _worker_setup(settings: Settings, metastore: Metastore) -> WorkerSetup:
    described = settings.describe_settings()
    context = JobContext(
        metastore,
        DataSourceProvider(settings, metastore),
        batch_size=settings.sync_batch_size,
        profile_sample_rows=settings.sync_profile_sample_rows,
        profile_max_age=timedelta(hours=settings.sync_profile_max_age_hours),
        llm=_worker_llm(described) if settings.describe_enabled else None,
        llm_model=_model_label(described),
        describe_sample_rows=settings.describe_sample_rows,
        embeddings=_worker_embeddings(settings),
        embedding_model=embedding_model_name(settings),
        embed_columns=settings.embeddings_columns,
    )
    return WorkerSetup(
        context,
        poll_interval=settings.sync_poll_interval_seconds,
        lease=timedelta(seconds=settings.sync_lease_seconds),
    )


def build_retriever(
    settings: Settings,
    metastore: Metastore,
    source: DataSourceInfo,
    embeddings: Embeddings | None = None,
) -> SchemaRetriever:
    return MetastoreRetriever(
        source.id,
        metastore.knowledge,
        metastore.embeddings,
        embeddings,
        embedding_model_name(settings),
        search_above_tables=settings.retrieval_search_above_tables,
        top_k=settings.retrieval_top_k,
        max_tables=settings.retrieval_max_tables,
        candidate_limit=settings.retrieval_candidates,
    )


def build_service(
    settings: Settings,
    metastore: Metastore,
    source: DataSourceInfo,
) -> AnalystService:
    llm = create_chat_model(settings)
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
        rewrite_question=settings.agent_rewrite_question,
        select_above=settings.retrieval_select_above,
        healer=heal,
    )
    return AnalystService(datasource, catalog, agent)


def _worker_llm(settings: Settings):
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


def build_tokens(metastore: Metastore, config: ConfigService) -> TokenService:
    return TokenService(
        metastore.signing_key(),
        lambda: timedelta(minutes=config.current().auth_token_minutes),
    )


def build_auth(metastore: Metastore, config: ConfigService) -> AuthService:
    return AuthService(metastore, build_tokens(metastore, config))


def build_registry(metastore: Metastore, config: ConfigService) -> AnalystRegistry:
    return AnalystRegistry(
        metastore,
        lambda source: build_service(config.current(), metastore, source),
        settings_version=config.version,
    )
