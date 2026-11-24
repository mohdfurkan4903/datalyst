import logging

import psycopg
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from datalyst import __version__
from datalyst.api.deps import Services
from datalyst.core.exceptions import MetastoreError

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def ready(app: Services) -> JSONResponse:
    try:
        pending = app.metastore.pending_migrations()
    except (psycopg.Error, PoolTimeout, MetastoreError):
        logger.warning("Metastore is not reachable", exc_info=True)
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    if pending:
        return JSONResponse(
            status_code=503,
            content={"status": "migrations pending", "pending": pending},
        )
    return JSONResponse(content={"status": "ready"})
