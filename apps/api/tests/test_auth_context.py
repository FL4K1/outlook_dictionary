"""Unit tests for AuthenticationContext immutability and permission checks."""

from __future__ import annotations

import uuid

import pytest

from app.auth.context import ANONYMOUS_CONTEXT, AuthenticationContext


@pytest.fixture
def sample_context() -> AuthenticationContext:
    """Create a standard AuthenticationContext for testing."""
    return AuthenticationContext(
        request_id="req_test",
        correlation_id="corr_test",
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        role_ids=frozenset({uuid.uuid4()}),
        permissions=frozenset({"tenant.read", "tenant.update", "user.read", "user.invite"}),
    )


@pytest.fixture
def empty_permissions_context() -> AuthenticationContext:
    """Create an AuthenticationContext with no permissions."""
    return AuthenticationContext(
        request_id="req_empty",
        correlation_id="corr_empty",
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        permissions=frozenset(),
    )


class TestAuthenticationContextImmutability:
    """Verify that AuthenticationContext is truly immutable."""

    def test_cannot_modify_user_id(self, sample_context: AuthenticationContext) -> None:
        with pytest.raises(AttributeError, match="cannot assign"):
            sample_context.user_id = uuid.uuid4()  # type: ignore[misc]

    def test_cannot_modify_tenant_id(self, sample_context: AuthenticationContext) -> None:
        with pytest.raises(AttributeError, match="cannot assign"):
            sample_context.tenant_id = uuid.uuid4()  # type: ignore[misc]

    def test_cannot_modify_correlation_id(self, sample_context: AuthenticationContext) -> None:
        with pytest.raises(AttributeError, match="cannot assign"):
            sample_context.correlation_id = "changed"  # type: ignore[misc]

    def test_cannot_modify_permissions(self, sample_context: AuthenticationContext) -> None:
        with pytest.raises(AttributeError, match="cannot assign"):
            sample_context.permissions = frozenset({"everything"})  # type: ignore[misc]

    def test_cannot_modify_session_id(self, sample_context: AuthenticationContext) -> None:
        with pytest.raises(AttributeError, match="cannot assign"):
            sample_context.session_id = uuid.uuid4()  # type: ignore[misc]

    def test_cannot_add_new_attribute(self, sample_context: AuthenticationContext) -> None:
        with pytest.raises(AttributeError):
            sample_context.new_field = "injected"  # type: ignore[attr-defined]


class TestAuthenticationContextPermissions:
    """Verify permission checking methods."""

    def test_has_permission_returns_true_for_granted(
        self, sample_context: AuthenticationContext
    ) -> None:
        assert sample_context.has_permission("tenant.read") is True

    def test_has_permission_returns_false_for_missing(
        self, sample_context: AuthenticationContext
    ) -> None:
        assert sample_context.has_permission("mail_account.manage") is False

    def test_has_any_permission_with_one_match(self, sample_context: AuthenticationContext) -> None:
        assert sample_context.has_any_permission("tenant.read", "nonexistent") is True

    def test_has_any_permission_with_no_match(self, sample_context: AuthenticationContext) -> None:
        assert sample_context.has_any_permission("admin.nuke", "admin.destroy") is False

    def test_has_all_permissions_when_all_present(
        self, sample_context: AuthenticationContext
    ) -> None:
        assert sample_context.has_all_permissions("tenant.read", "user.read") is True

    def test_has_all_permissions_when_one_missing(
        self, sample_context: AuthenticationContext
    ) -> None:
        assert sample_context.has_all_permissions("tenant.read", "admin.nuke") is False

    def test_empty_permissions_always_deny(
        self, empty_permissions_context: AuthenticationContext
    ) -> None:
        assert empty_permissions_context.has_permission("anything") is False
        assert empty_permissions_context.has_any_permission("a", "b") is False
        assert empty_permissions_context.has_all_permissions("a") is False


class TestAuthenticationContextConstruction:
    """Verify correct default values and construction."""

    def test_default_is_not_service_account(self, sample_context: AuthenticationContext) -> None:
        assert sample_context.is_service_account is False

    def test_service_account_flag(self) -> None:
        ctx = AuthenticationContext(
            request_id="req_service",
            correlation_id="corr_service",
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            is_service_account=True,
        )
        assert ctx.is_service_account is True

    def test_default_permissions_is_empty_frozenset(self) -> None:
        ctx = AuthenticationContext(
            request_id="req_default",
            correlation_id="corr_default",
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )
        assert ctx.permissions == frozenset()

    def test_anonymous_context_sentinel_is_none(self) -> None:
        assert ANONYMOUS_CONTEXT is None
