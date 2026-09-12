"""Structured error responses and global exception handlers."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_conf import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """Domain error carrying a stable machine-readable code and HTTP status.

    Raised by dependencies and handlers when a request violates a business rule
    (missing auth, wrong role, suspended/over-quota tenant, ...). The global
    handler turns it into the ``{"error": {code, message, ...}}`` envelope with a
    *specific* code rather than the status-derived default (e.g. 403 ->
    ``TENANT_SUSPENDED`` instead of ``FORBIDDEN``).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


# HTTP status → stable, machine-readable error code string.
_STATUS_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    """Build the platform error envelope ``{"error": {"code", "message", …}}``."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


def register_exception_handlers(app: FastAPI) -> None:
    """Register the global exception handlers on ``app``."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_CODE_MAP.get(exc.status_code, "ERROR")
        detail = exc.detail
        if isinstance(detail, str):
            message, details = detail, None
        else:
            message, details = "Request failed", detail
        return error_response(exc.status_code, code, message, details)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(422, "VALIDATION_ERROR", "Request validation failed", exc.errors())

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log with the full traceback; the client only sees a generic message.
        log.exception("unhandled_exception")
        return error_response(500, "INTERNAL_ERROR", "Internal server error")
