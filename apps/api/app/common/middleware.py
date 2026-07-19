"""Request middleware — request ID injection, logging, and security headers.

Middleware execution order (outermost → innermost):
1. Request ID injection (adds X-Request-ID to every request/response)
2. Request logging (logs method, path, status, duration)
3. CORS (handled by FastAPI's CORSMiddleware, configured in main.py)
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, ClassVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request.

    - Reads X-Request-ID from incoming headers (if provided by a load balancer).
    - Generates a new UUID if not present.
    - Stores it in request.state.request_id for downstream use.
    - Adds it to the response headers.
    - Binds it to structlog's contextvars for automatic inclusion in all logs.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        # Bind request_id to structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status code, and duration.

    Skips health check endpoints to avoid log noise.
    """

    SKIP_PATHS: ClassVar[set[str]] = {"/health/live", "/health/ready", "/health/deep"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        logger = structlog.get_logger("http")
        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            client_ip=request.client.host if request.client else None,
        )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response
