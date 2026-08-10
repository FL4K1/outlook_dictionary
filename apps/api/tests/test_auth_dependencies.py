"""Unit tests for auth dependencies."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request

from app.auth.context import AuthenticationContext
from app.auth.dependencies import (
    get_auth_context,
    require_permission,
    require_role,
    require_tenant_membership,
)
from app.auth.exceptions import AuthenticationError, InsufficientPermissionsError


@pytest.fixture
def auth_context() -> AuthenticationContext:
    return AuthenticationContext(
        request_id="req_test",
        correlation_id="corr_test",
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        role_ids=frozenset({uuid.uuid4()}),
        role_names=frozenset({"admin"}),
        permissions=frozenset({"mail_account.read", "mail_account.write"}),
    )


def _make_request(auth_context: AuthenticationContext | None = None) -> MagicMock:
    request = MagicMock(spec=Request)
    request.state.auth_context = auth_context
    return request


class TestGetAuthContext:
    """Verify get_auth_context dependency."""

    async def test_returns_context(self, auth_context: AuthenticationContext) -> None:
        request = _make_request(auth_context)
        result = await get_auth_context(request)
        assert result is auth_context

    async def test_raises_when_missing(self) -> None:
        request = _make_request(None)
        with pytest.raises(AuthenticationError, match="No authentication context"):
            await get_auth_context(request)


class TestRequirePermission:
    """Verify require_permission dependency factory."""

    async def test_passes_with_matching_permission(
        self,
        auth_context: AuthenticationContext,
    ) -> None:
        dependency = require_permission("mail_account.read")
        with patch("app.auth.dependencies.PolicyEngine") as mock_policy:
            mock_engine = mock_policy.return_value
            mock_engine.authorize.return_value = MagicMock(allowed=True)
            result = await dependency(auth_context)  # type: ignore[misc]
        assert result is auth_context

    async def test_raises_with_missing_permission(
        self,
        auth_context: AuthenticationContext,
    ) -> None:
        dependency = require_permission("admin.nuke")
        with patch("app.auth.dependencies.PolicyEngine") as mock_policy:
            mock_engine = mock_policy.return_value
            mock_engine.authorize.return_value = MagicMock(allowed=False)
            with pytest.raises(InsufficientPermissionsError):
                await dependency(auth_context)  # type: ignore[misc]


class TestRequireRole:
    """Verify require_role dependency factory."""

    async def test_passes_with_matching_role(
        self,
        auth_context: AuthenticationContext,
    ) -> None:
        dependency = require_role("admin")
        result = await dependency(auth_context)  # type: ignore[misc]
        assert result is auth_context

    async def test_raises_with_missing_role(
        self,
        auth_context: AuthenticationContext,
    ) -> None:
        dependency = require_role("superadmin")
        with pytest.raises(InsufficientPermissionsError):
            await dependency(auth_context)  # type: ignore[misc]


class TestRequireTenantMembership:
    """Verify require_tenant_membership dependency factory."""

    async def test_passes_with_membership(
        self,
        auth_context: AuthenticationContext,
    ) -> None:
        dependency = require_tenant_membership()
        result = await dependency(auth_context)  # type: ignore[misc]
        assert result is auth_context

    async def test_raises_without_membership(self) -> None:
        context = AuthenticationContext(
            request_id="req_test",
            correlation_id="corr_test",
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            membership_id=None,
            role_ids=frozenset(),
            permissions=frozenset(),
        )
        dependency = require_tenant_membership()
        with pytest.raises(AuthenticationError, match="No active tenant membership"):
            await dependency(context)  # type: ignore[misc]
