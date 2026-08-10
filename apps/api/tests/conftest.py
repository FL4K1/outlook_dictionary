"""Test configuration and shared fixtures for the API test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.config import Environment, LogFormat, Settings
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
def test_settings() -> Settings:
    """Provide test-specific settings that don't require real infrastructure."""
    return Settings(
        app_env=Environment.TESTING,
        app_debug=False,
        app_log_level="WARNING",
        app_log_format=LogFormat.CONSOLE,
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test_mail_intelligence",
    )


@pytest.fixture
async def client(test_settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for testing API endpoints.

    Uses httpx's ASGITransport to send requests directly to the FastAPI
    app without starting a real server. This makes tests fast and isolated.

    Note: The database dependency is NOT available in this fixture.
    Tests that need database access should use a separate fixture
    with testcontainers (added in Milestone 1 when real DB tests begin).
    """
    app = create_app(settings=test_settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_session() -> AsyncGenerator[None, None]:
    """Provide a database session for testing.

    Currently deferred until PostgreSQL testcontainers are configured
    in Milestone 1 (consistent with PR-1.2.3 integration testing strategy).
    """
    pytest.skip("Requires PostgreSQL testcontainers (deferred per PR-1.2.3)")
    yield
