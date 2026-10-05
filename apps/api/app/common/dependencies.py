"""Dependency injection via FastAPI's Depends() system.

This module provides request-scoped and application-scoped dependencies.
Each dependency is a function that FastAPI calls per-request, enabling
clean testability (override in tests) without a heavy DI framework.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.common.config import Settings, get_settings
from mip_models.database import AsyncSessionFactory, get_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as redis

# ---------------------------------------------------------------------------
# Application-scoped singletons (initialized once at startup)
# ---------------------------------------------------------------------------

_engine: Any | None = None
_session_factory: AsyncSessionFactory | None = None
_redis_client: Any | None = None


def init_dependencies(settings: Settings) -> None:
    """Initialize application-scoped dependencies.

    Called once during the application lifespan startup event.
    """
    global _engine, _session_factory, _redis_client
    _engine = get_async_engine(
        settings.database_url,
        echo=settings.app_debug,
    )
    _session_factory = AsyncSessionFactory(_engine)


    _redis_client = redis.from_url(settings.redis_url, decode_responses=True)


async def shutdown_dependencies() -> None:
    """Clean up application-scoped dependencies.

    Called once during the application lifespan shutdown event.
    """
    global _engine, _session_factory, _redis_client
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_factory = None

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None


def get_engine() -> Any | None:
    """Return the application-scoped database engine for testing/observability."""
    return _engine


# ---------------------------------------------------------------------------
# Request-scoped dependencies (injected per-request via Depends())
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for the current request.

    Usage in a route::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Tenant))
            ...
    """
    if _session_factory is None:
        msg = "Database session factory not initialized. Call init_dependencies() first."
        raise RuntimeError(msg)
    async for session in _session_factory.get_session():
        yield session


def get_current_settings() -> Settings:
    """Provide the application settings for the current request.

    This is a thin wrapper around get_settings() to make it
    injectable and overridable in tests.
    """
    return get_settings()


def get_session_factory() -> AsyncSessionFactory | None:
    """Return the application-scoped session factory.

    Used by middleware and other application-scoped components that
    need to create per-request database sessions outside of FastAPI's
    dependency injection system.
    """
    return _session_factory


def get_redis() -> Any:
    """Return the application-scoped Redis client.

    Used by rate limiting and cache operations.
    """
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized.")
    return _redis_client
