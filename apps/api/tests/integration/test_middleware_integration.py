"""Integration tests for AuthenticationMiddleware.

These tests require a live PostgreSQL database and are deferred
until testcontainers are configured (consistent with PR-1.2.3).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="Integration tests require live PostgreSQL (deferred per PR-1.2.3)"
)


class TestMiddlewareIntegration:
    """Integration tests for the full auth flow."""

    async def test_full_auth_flow_success(self) -> None:
        pytest.skip("Requires PostgreSQL testcontainers")

    async def test_full_auth_flow_session_expired(self) -> None:
        pytest.skip("Requires PostgreSQL testcontainers")

    async def test_full_auth_flow_tenant_mismatch(self) -> None:
        pytest.skip("Requires PostgreSQL testcontainers")

    async def test_refresh_token_end_to_end(self) -> None:
        pytest.skip("Requires PostgreSQL testcontainers and PR-1.2.5 auth APIs")
