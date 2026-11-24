import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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
    brief,
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

# Server-side failures get a fixed message: their details (provider responses,
# connection strings, stack traces) stay in the server log.
SERVER_MESSAGES: list[tuple[type[Exception], str]] = [
    (
        LLMError,
        (
            "The AI model could not answer. Try again; if it keeps failing, an "
            "admin can check the model on the Settings page."
        ),
    ),
    (MetastoreError, "Datalyst's database is unavailable. Try again in a moment."),
]
UNEXPECTED = "Something went wrong on the server. Try again, or ask an admin."


def status_for(error: DatalystError) -> int:
    return next((code for kind, code in STATUS_CODES if isinstance(error, kind)), 500)


def public_message(error: Exception) -> str:
    """What the caller sees for an error: the reason for client errors only."""
    if isinstance(error, DatalystError) and status_for(error) < 500:
        return brief(error)
    return next(
        (text for kind, text in SERVER_MESSAGES if isinstance(error, kind)), UNEXPECTED
    )


def _response(request: Request, status: int, kind: str, message: str) -> JSONResponse:
    reference = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status,
        content={"error": {"type": kind, "message": message, "reference": reference}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DatalystError)
    async def datalyst_error(request: Request, error: DatalystError) -> JSONResponse:
        status = status_for(error)
        if status >= 500:
            logger.error(
                "%s %s failed",
                request.method,
                request.url.path,
                exc_info=(type(error), error, error.__traceback__),
            )
        return _response(request, status, type(error).__name__, public_message(error))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        problems = []
        for item in error.errors():
            field = ".".join(str(part) for part in item.get("loc", ())[1:])
            problems.append(f"{field}: {item.get('msg')}" if field else item.get("msg"))
        return _response(
            request,
            422,
            "ValidationError",
            brief("; ".join(problems) or "Invalid request."),
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.error(
            "%s %s failed unexpectedly",
            request.method,
            request.url.path,
            exc_info=(type(error), error, error.__traceback__),
        )
        return _response(request, 500, "InternalError", UNEXPECTED)
