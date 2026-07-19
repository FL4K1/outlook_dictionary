"""Async database engine and session management.

Provides factory functions for creating async SQLAlchemy engines and sessions.
Configuration values are injected at startup — this module does not read
environment variables directly.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def get_async_engine(
    database_url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    echo: bool = False,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: PostgreSQL connection string using asyncpg driver.
            Format: ``postgresql+asyncpg://user:pass@host:port/dbname``
        pool_size: Number of persistent connections in the pool.
        max_overflow: Maximum additional connections beyond pool_size.
        echo: If True, log all SQL statements (for development only).

    Returns:
        A configured AsyncEngine instance.
    """
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
    )


class AsyncSessionFactory:
    """Manages async database sessions.

    Intended to be instantiated once at application startup and injected
    via FastAPI's dependency system.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a database session for use in a request scope.

        The session is committed on success, rolled back on exception,
        and closed in all cases.
        """
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
