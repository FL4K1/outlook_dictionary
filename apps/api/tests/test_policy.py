"""Unit tests for PolicyEngine."""

from __future__ import annotations

import uuid

import pytest

from app.auth.context import AuthenticationContext
from app.auth.policy import PolicyEngine


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


@pytest.fixture
def sample_context() -> AuthenticationContext:
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


class TestPolicyEnginePublicRoutes:
    """Verify is_public_route behavior."""

    def test_health_live_is_public(self, policy_engine: PolicyEngine) -> None:
        assert policy_engine.is_public_route("/health/live", "GET") is True

    def test_protected_route_is_not_public(self, policy_engine: PolicyEngine) -> None:
        assert policy_engine.is_public_route("/api/v1/mail/accounts", "GET") is False


class TestPolicyEngineAuthorize:
    """Verify authorize() authorization decisions."""

    def test_authorize_permission_pass(
        self,
        policy_engine: PolicyEngine,
        sample_context: AuthenticationContext,
    ) -> None:
        decision = policy_engine.authorize(sample_context, "mail_account", "read")
        assert decision.allowed is True
        assert decision.reason is None

    def test_authorize_permission_fail(
        self,
        policy_engine: PolicyEngine,
        sample_context: AuthenticationContext,
    ) -> None:
        decision = policy_engine.authorize(sample_context, "admin", "nuke")
        assert decision.allowed is False
        assert decision.reason == "Insufficient permissions"
        assert "admin.nuke" in decision.missing_permissions

    def test_authorize_resource_owner_pass(
        self,
        policy_engine: PolicyEngine,
        sample_context: AuthenticationContext,
    ) -> None:
        owner_id = sample_context.user_id
        decision = policy_engine.authorize(
            sample_context, "admin", "nuke", resource_owner_id=owner_id
        )
        assert decision.allowed is True
        assert decision.reason == "Resource owner"

    def test_authorize_resource_owner_fail(
        self,
        policy_engine: PolicyEngine,
        sample_context: AuthenticationContext,
    ) -> None:
        decision = policy_engine.authorize(
            sample_context, "admin", "nuke", resource_owner_id=uuid.uuid4()
        )
        assert decision.allowed is False

    def test_authorize_empty_permissions_deny(
        self,
        policy_engine: PolicyEngine,
        sample_context: AuthenticationContext,
    ) -> None:
        empty_context = AuthenticationContext(
            request_id="req_test",
            correlation_id="corr_test",
            user_id=sample_context.user_id,
            tenant_id=sample_context.tenant_id,
            organization_id=sample_context.organization_id,
            session_id=sample_context.session_id,
            membership_id=sample_context.membership_id,
            role_ids=frozenset(),
            permissions=frozenset(),
        )
        decision = policy_engine.authorize(empty_context, "mail_account", "read")
        assert decision.allowed is False
        assert decision.reason == "No permissions granted"

    def test_policy_engine_has_repo_deps(self) -> None:
        engine = PolicyEngine(
            membership_repo=object(),
            role_repo=object(),
            permission_repo=object(),
        )
        assert engine._membership_repo is not None
        assert engine._role_repo is not None
        assert engine._permission_repo is not None

    def test_authorize_returns_immutable_decision(
        self,
        policy_engine: PolicyEngine,
        sample_context: AuthenticationContext,
    ) -> None:
        decision = policy_engine.authorize(sample_context, "mail_account", "read")
        with pytest.raises(AttributeError):
            decision.allowed = False  # type: ignore[misc]
