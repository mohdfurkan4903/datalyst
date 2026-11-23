import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

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
    settings,
    sources,
)
from datalyst.config import BootSettings, get_boot_settings
from datalyst.metastore import Metastore
from datalyst.services import AnalystRegistry, ConfigService, QuestionService

logger = logging.getLogger("datalyst.api")


def build_services(
    boot: BootSettings,
    metastore: Metastore,
    config: ConfigService | None = None,
    registry: AnalystRegistry | None = None,
) -> AppServices:
    from datalyst.bootstrap import build_auth, build_registry

    config = config or ConfigService(metastore)
    registry = registry or build_registry(metastore, config)
    return AppServices(
        metastore=metastore,
        config=config,
        auth=build_auth(boot, metastore, config),
        registry=registry,
        questions=QuestionService(metastore, registry),
        boot=boot,
    )


def create_app(services: AppServices | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        if owned:
            from datalyst.bootstrap import open_ready_metastore

            boot = get_boot_settings()
            boot.require_secret_key()
            metastore = open_ready_metastore(boot)
            app.state.services = build_services(boot, metastore)
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
    for module in (
        health,
        auth,
        sources,
        catalog,
        knowledge,
        questions,
        admin,
        settings,
    ):
        app.include_router(module.router)
    return app
