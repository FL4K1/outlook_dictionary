"""FastAPI application factory.

Uses the factory pattern (create_app) so that:
- Tests can create isolated app instances with custom settings.
- uvicorn can call it via --factory flag.
- Lifespan events manage resource lifecycle cleanly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth.entra_router import router as entra_router
from app.api.auth.router import router as auth_router
from app.auth.middleware import AuthenticationMiddleware
from app.auth.policy import PolicyEngine
from app.auth.tokens import TokenService
from app.common.config import Environment, Settings, get_settings
from app.common.dependencies import init_dependencies, shutdown_dependencies
from app.common.exceptions import register_exception_handlers
from app.common.logging import get_logger, setup_logging
from app.common.middleware import (
    RequestIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.health.router import router as health_router

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle.

    - Startup: Initialize database connections, logging, registries.
    - Shutdown: Close connections, flush buffers.
    """
    settings: Settings = app.state.settings
    logger = get_logger("lifespan")

    logger.info(
        "application_starting",
        environment=settings.app_env.value,
        debug=settings.app_debug,
    )

    if settings.app_env != Environment.TESTING:
        init_dependencies(settings)

    logger.info("application_started")

    yield

    # Shutdown
    logger.info("application_stopping")
    await shutdown_dependencies()
    logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional settings override. If None, reads from environment.
                  Pass custom settings in tests.

    Returns:
        A fully configured FastAPI application instance.
    """
    if settings is None:
        settings = get_settings()

    # Configure structured logging before anything else
    setup_logging(settings.app_log_level, settings.app_log_format)

    app = FastAPI(
        title="Mail Intelligence Platform",
        description="Enterprise mail intelligence with multi-stage search and AI reasoning.",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # Store settings on app state for access during lifespan
    app.state.settings = settings

    # --- Middleware (outermost → innermost) ---
    # Note: FastAPI applies middleware in reverse registration order,
    # so register innermost first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        AuthenticationMiddleware,  # type: ignore[arg-type]
        policy_engine=PolicyEngine(),
        token_service=TokenService(settings),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Exception Handlers ---
    register_exception_handlers(app)

    # --- Routers ---
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(entra_router)

    return app
