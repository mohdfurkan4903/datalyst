import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from datalyst import __version__
from datalyst.api.deps import AppServices
from datalyst.api.errors import install_error_handlers
from datalyst.api.routers import (
    admin,
    auth,
    catalog,
    health,
    knowledge,
    questions,
    sources,
)
from datalyst.config import Settings, get_settings
from datalyst.core.exceptions import ConfigurationError
from datalyst.metastore import Metastore
from datalyst.services import AnalystRegistry, QuestionService

logger = logging.getLogger("datalyst.api")


def build_services(
    settings: Settings,
    metastore: Metastore,
    registry: AnalystRegistry | None = None,
) -> AppServices:
    from datalyst.bootstrap import build_auth, build_registry

    registry = registry or build_registry(settings, metastore)
    return AppServices(
        settings=settings,
        metastore=metastore,
        auth=build_auth(settings, metastore),
        registry=registry,
        questions=QuestionService(metastore, registry),
    )


def create_app(
    settings: Settings | None = None, services: AppServices | None = None
) -> FastAPI:
    settings = settings or (services.settings if services else get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        if owned:
            from datalyst.bootstrap import open_metastore

            metastore = open_metastore(settings)
            if pending := metastore.pending_migrations():
                metastore.close()
                raise ConfigurationError(
                    f"Metastore needs migrations ({', '.join(pending)}). Run: datalyst migrate"
                )
            app.state.services = build_services(settings, metastore)
        else:
            app.state.services = services
        try:
            yield
        finally:
            if owned:
                app.state.services.registry.close()
                app.state.services.metastore.close()

    app = FastAPI(title="Datalyst", version=__version__, lifespan=lifespan)
    if services is not None:
        app.state.services = services
    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.api_cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info(
            "%s %s %s %.0fms id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response

    install_error_handlers(app)
    for module in (health, auth, sources, catalog, knowledge, questions, admin):
        app.include_router(module.router)
    return app
