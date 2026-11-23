import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from datalyst.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DatalystError,
    LLMError,
    MetastoreError,
    NotFoundError,
    PermissionDeniedError,
    QueryExecutionError,
    UnsafeQueryError,
)

logger = logging.getLogger(__name__)

STATUS_CODES: list[tuple[type[DatalystError], int]] = [
    (AuthenticationError, 401),
    (PermissionDeniedError, 403),
    (NotFoundError, 404),
    (ConfigurationError, 400),
    (UnsafeQueryError, 400),
    (QueryExecutionError, 422),
    (LLMError, 502),
    (MetastoreError, 503),
]


def status_for(error: DatalystError) -> int:
    return next((code for kind, code in STATUS_CODES if isinstance(error, kind)), 500)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DatalystError)
    async def datalyst_error(request: Request, error: DatalystError) -> JSONResponse:
        status = status_for(error)
        if status >= 500:
            logger.error("%s %s failed: %s", request.method, request.url.path, error)
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        return JSONResponse(
            status_code=status,
            content={"error": {"type": type(error).__name__, "message": str(error)}},
            headers=headers,
        )
