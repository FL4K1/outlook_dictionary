"""Dependency injection via FastAPI's Depends() system.

This module provides request-scoped and application-scoped dependencies.
Each dependency is a function that FastAPI calls per-request, enabling
clean testability (override in tests) without a heavy DI framework.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.common.config import Settings, get_settings
from mip_models.database import AsyncSessionFactory, get_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Application-scoped singletons (initialized once at startup)
# ---------------------------------------------------------------------------

_session_factory: AsyncSessionFactory | None = None


def init_dependencies(settings: Settings) -> None:
    """Initialize application-scoped dependencies.

    Called once during the application lifespan startup event.
    """
    global _session_factory
    engine = get_async_engine(
        settings.database_url,
        echo=settings.app_debug,
    )
    _session_factory = AsyncSessionFactory(engine)


async def shutdown_dependencies() -> None:
    """Clean up application-scoped dependencies.

    Called once during the application lifespan shutdown event.
    """
    global _session_factory
    _session_factory = None


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
