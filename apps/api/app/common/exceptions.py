"""Centralized exception handling and error response schema.

All exceptions raised within the application are caught by the global
handlers registered in this module and converted to a consistent JSON
error response format matching the API Blueprint (Section 10.3).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.common.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Error Response Schema
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Standard error response body, consistent across all endpoints."""

    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Wrapper matching the API Blueprint error format."""

    error: ErrorDetail


# ---------------------------------------------------------------------------
# Application Exception Classes
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base exception for all application-level errors.

    Subclass this for domain-specific errors. Each subclass defines its
    own HTTP status code and error code string.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details
        super().__init__(self.message)


class NotFoundError(AppError):
    """Resource not found."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"
    message = "The requested resource was not found."


class ConflictError(AppError):
    """Resource conflict (e.g., duplicate slug)."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "CONFLICT"
    message = "The request conflicts with the current state of the resource."


class ForbiddenError(AppError):
    """Insufficient permissions."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "FORBIDDEN"
    message = "You do not have permission to perform this action."


class UnauthorizedError(AppError):
    """Authentication required or invalid."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "UNAUTHORIZED"
    message = "Authentication is required."


class BadRequestError(AppError):
    """Client sent an invalid request."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "BAD_REQUEST"
    message = "The request is invalid."


class RateLimitError(AppError):
    """Rate limit exceeded."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "Rate limit exceeded. Please retry after some time."


# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------


def _build_error_response(
    request: Request,
    status_code: int,
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a consistent JSON error response."""
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        error=ErrorDetail(
            code=error_code,
            message=message,
            details=details,
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle all AppError subclasses."""
    logger.warning(
        "app_error",
        error_code=exc.error_code,
        message=exc.message,
        status_code=exc.status_code,
    )
    return _build_error_response(
        request,
        status_code=exc.status_code,
        error_code=exc.error_code,
        message=exc.message,
        details=exc.details,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors from request parsing."""
    errors = exc.errors()
    logger.warning("validation_error", error_count=len(errors))
    return _build_error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_code="VALIDATION_ERROR",
        message="Request validation failed.",
        details={"errors": errors},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions. Prevents stack traces from leaking."""
    logger.exception("unhandled_error", exc_type=type(exc).__name__)
    return _build_error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="INTERNAL_ERROR",
        message="An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI application."""
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)  # type: ignore[arg-type]
