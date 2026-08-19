"""Unit tests for identity provider repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.identity_provider import (
    EntraTenantMappingRepository,
    IdentityProviderCredentialRepository,
    OAuthStateRepository,
)
from mip_models.identity_provider import (
    EntraTenantMapping,
    IdentityProviderCredential,
    OAuthState,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


def _setup_execute_result(mock_session: MagicMock, result: object) -> None:
    mock_result = MagicMock()
    if isinstance(result, list):
        mock_result.scalars.return_value.all.return_value = result
        mock_result.scalars.return_value.first.return_value = result[0] if result else None
    else:
        mock_result.scalars.return_value.first.return_value = result
        mock_result.scalars.return_value.all.return_value = [result] if result else []
    mock_session.execute.return_value = mock_result


@pytest.fixture
def oauth_state_repo(mock_session: MagicMock) -> OAuthStateRepository:
    return OAuthStateRepository(mock_session)


@pytest.fixture
def credential_repo(mock_session: MagicMock) -> IdentityProviderCredentialRepository:
    return IdentityProviderCredentialRepository(mock_session)


@pytest.fixture
def tenant_mapping_repo(mock_session: MagicMock) -> EntraTenantMappingRepository:
    return EntraTenantMappingRepository(mock_session)


# ---------------------------------------------------------------------------
# OAuthStateRepository tests
# ---------------------------------------------------------------------------


class TestOAuthStateRepository:
    """Tests for OAuthStateRepository."""

    async def test_create_state(
        self,
        oauth_state_repo: OAuthStateRepository,
        mock_session: MagicMock,
    ) -> None:
        expires = datetime.now(UTC)
        request_id = str(uuid.uuid4())

        fake_state = OAuthState(
            id=uuid.uuid4(),
            state="test-state",
            nonce="test-nonce",
            code_verifier="test-verifier",
            provider="microsoft",
            expires_at=expires,
            consumed_at=None,
            request_id=request_id,
        )
        mock_session.flush.return_value = None

        async def mock_create(**kwargs: object) -> OAuthState:
            return fake_state

        oauth_state_repo.create = mock_create  # type: ignore[method-assign]

        result = await oauth_state_repo.create_state(
            state="test-state",
            nonce="test-nonce",
            code_verifier="test-verifier",
            provider="microsoft",
            expires_at=expires,
            request_id=request_id,
        )

        assert result.state == "test-state"
        assert result.nonce == "test-nonce"
        assert result.code_verifier == "test-verifier"
        assert result.provider == "microsoft"
        assert result.consumed_at is None

    async def test_get_by_state_found(
        self,
        oauth_state_repo: OAuthStateRepository,
        mock_session: MagicMock,
    ) -> None:
        fake_state = OAuthState(
            id=uuid.uuid4(),
            state="test-state",
            nonce="test-nonce",
            code_verifier="test-verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
        )
        _setup_execute_result(mock_session, fake_state)

        result = await oauth_state_repo.get_by_state("test-state")

        assert result == fake_state

    async def test_get_by_state_not_found(
        self,
        oauth_state_repo: OAuthStateRepository,
        mock_session: MagicMock,
    ) -> None:
        _setup_execute_result(mock_session, None)

        result = await oauth_state_repo.get_by_state("nonexistent")

        assert result is None

    async def test_consume_state_success(
        self,
        oauth_state_repo: OAuthStateRepository,
        mock_session: MagicMock,
    ) -> None:
        fake_state = OAuthState(
            id=uuid.uuid4(),
            state="test-state",
            nonce="test-nonce",
            code_verifier="test-verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        _setup_execute_result(mock_session, fake_state)

        result = await oauth_state_repo.consume_state("test-state")

        assert result == fake_state
        mock_session.flush.assert_called_once()

    async def test_consume_state_not_found(
        self,
        oauth_state_repo: OAuthStateRepository,
        mock_session: MagicMock,
    ) -> None:
        _setup_execute_result(mock_session, None)

        result = await oauth_state_repo.consume_state("nonexistent")

        assert result is None

    async def test_get_unconsumed(
        self,
        oauth_state_repo: OAuthStateRepository,
        mock_session: MagicMock,
    ) -> None:
        fake_state = OAuthState(
            id=uuid.uuid4(),
            state="test-state",
            nonce="test-nonce",
            code_verifier="test-verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        _setup_execute_result(mock_session, fake_state)

        result = await oauth_state_repo.get_unconsumed("test-state")

        assert result == fake_state


# ---------------------------------------------------------------------------
# IdentityProviderCredentialRepository tests
# ---------------------------------------------------------------------------


class TestIdentityProviderCredentialRepository:
    """Tests for IdentityProviderCredentialRepository."""

    async def test_get_by_identity_id_found(
        self,
        credential_repo: IdentityProviderCredentialRepository,
        mock_session: MagicMock,
    ) -> None:
        identity_id = uuid.uuid4()
        fake_cred = IdentityProviderCredential(
            id=uuid.uuid4(),
            identity_id=identity_id,
            tenant_id=uuid.uuid4(),
            provider="microsoft",
            encrypted_access_token=b"encrypted-access",
            encrypted_refresh_token=b"encrypted-refresh",
            token_expires_at=datetime.now(UTC),
            encryption_key_id="dek-v1",
        )
        _setup_execute_result(mock_session, fake_cred)

        result = await credential_repo.get_by_identity_id(identity_id)

        assert result == fake_cred

    async def test_get_by_identity_id_not_found(
        self,
        credential_repo: IdentityProviderCredentialRepository,
        mock_session: MagicMock,
    ) -> None:
        _setup_execute_result(mock_session, None)

        result = await credential_repo.get_by_identity_id(uuid.uuid4())

        assert result is None

    async def test_revoke_success(
        self,
        credential_repo: IdentityProviderCredentialRepository,
        mock_session: MagicMock,
    ) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        result = await credential_repo.revoke(uuid.uuid4(), datetime.now(UTC))

        assert result is True
        mock_session.flush.assert_called_once()

    async def test_revoke_not_found(
        self,
        credential_repo: IdentityProviderCredentialRepository,
        mock_session: MagicMock,
    ) -> None:
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        result = await credential_repo.revoke(uuid.uuid4(), datetime.now(UTC))

        assert result is False


# ---------------------------------------------------------------------------
# EntraTenantMappingRepository tests
# ---------------------------------------------------------------------------


class TestEntraTenantMappingRepository:
    """Tests for EntraTenantMappingRepository."""

    async def test_get_by_entra_tenant_id_found(
        self,
        tenant_mapping_repo: EntraTenantMappingRepository,
        mock_session: MagicMock,
    ) -> None:
        fake_mapping = EntraTenantMapping(
            id=uuid.uuid4(),
            entra_tenant_id="tenant-123",
            tenant_id=uuid.uuid4(),
            is_active=True,
        )
        _setup_execute_result(mock_session, fake_mapping)

        result = await tenant_mapping_repo.get_by_entra_tenant_id("tenant-123")

        assert result == fake_mapping

    async def test_get_by_entra_tenant_id_not_found(
        self,
        tenant_mapping_repo: EntraTenantMappingRepository,
        mock_session: MagicMock,
    ) -> None:
        _setup_execute_result(mock_session, None)

        result = await tenant_mapping_repo.get_by_entra_tenant_id("nonexistent")

        assert result is None

    async def test_get_active_mappings(
        self,
        tenant_mapping_repo: EntraTenantMappingRepository,
        mock_session: MagicMock,
    ) -> None:
        fake_mappings = [
            EntraTenantMapping(
                id=uuid.uuid4(),
                entra_tenant_id="t1",
                tenant_id=uuid.uuid4(),
                is_active=True,
            ),
            EntraTenantMapping(
                id=uuid.uuid4(),
                entra_tenant_id="t2",
                tenant_id=uuid.uuid4(),
                is_active=True,
            ),
        ]
        _setup_execute_result(mock_session, fake_mappings)

        result = await tenant_mapping_repo.get_active_mappings()

        assert result == fake_mappings
