import psycopg
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from datalyst import __version__
from datalyst.api.deps import Services
from datalyst.core.exceptions import MetastoreError

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def ready(app: Services) -> JSONResponse:
    try:
        pending = app.metastore.pending_migrations()
    except (psycopg.Error, PoolTimeout, MetastoreError) as e:
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "detail": str(e)}
        )
    if pending:
        return JSONResponse(
            status_code=503,
            content={"status": "migrations pending", "pending": pending},
        )
    return JSONResponse(content={"status": "ready"})
