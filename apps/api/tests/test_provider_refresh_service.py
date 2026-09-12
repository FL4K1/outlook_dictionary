import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.events import SecurityEventType
from app.services.identity_provider import ProviderAuthError, ProviderAuthService
from mip_models.identity_provider import IdentityProviderCredential
from mip_providers.identity.base import ProviderCredentialSet

REPO_PATH = "app.services.identity_provider.IdentityProviderCredentialRepository"


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def mock_provider_auth():
    auth = AsyncMock()
    auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="new-access-token",
        refresh_token="new-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["openid", "offline_access"],
    )
    return auth


@pytest.fixture
def mock_encryption_service():
    enc = MagicMock()
    enc.decrypt.return_value = b"plain-old-refresh-token"
    enc.decrypt_string.return_value = "plain-old-refresh-token"
    enc.encrypt.side_effect = lambda x: f"encrypted-{x}".encode()
    enc.key_id = "key-v1"
    return enc


@pytest.fixture
def provider_service(mock_provider_auth, mock_encryption_service):
    return ProviderAuthService(
        provider_auth=mock_provider_auth,
        settings=MagicMock(),
        encryption_service=mock_encryption_service,
    )


@pytest.mark.asyncio
async def test_successful_credential_refresh_flow(
    provider_service, mock_db, mock_encryption_service, mock_provider_auth
):
    """Test successful refresh: tokens encrypted, key_id & expiry updated, flush called."""
    identity_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    future_expiry = datetime.now(UTC) + timedelta(hours=1)

    mock_credential = IdentityProviderCredential(
        id=credential_id,
        identity_id=identity_id,
        encrypted_access_token=b"enc-old-access",
        encrypted_refresh_token=b"enc-old-refresh",
        scopes=["openid"],
        revoked_at=None,
    )

    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="fresh-access-token",
        refresh_token="fresh-refresh-token",
        expires_at=future_expiry,
        scopes=["openid", "profile"],
    )

    with patch(
        "app.services.identity_provider.IdentityProviderCredentialRepository"
    ) as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_identity_id = AsyncMock(return_value=mock_credential)

        await provider_service.refresh_provider_credentials(
            mock_db, identity_id, request_id="req-100"
        )

        # 15. Tenant/identity isolation
        mock_repo.get_by_identity_id.assert_awaited_once_with(identity_id)
        mock_encryption_service.decrypt_string.assert_called_with(b"enc-old-refresh")
        mock_provider_auth.refresh_credentials.assert_awaited_once_with("plain-old-refresh-token")
        token_arg = mock_provider_auth.refresh_credentials.call_args[0][0]
        assert isinstance(token_arg, str)
        assert not isinstance(token_arg, bytes)

        # 2 & 3. Encrypted before persistence
        assert mock_credential.encrypted_access_token == b"encrypted-fresh-access-token"
        assert mock_credential.encrypted_refresh_token == b"encrypted-fresh-refresh-token"

        # 4 & 5. Key ID and expiry updated
        assert mock_credential.encryption_key_id == "key-v1"
        assert mock_credential.token_expires_at == future_expiry
        assert mock_credential.scopes is not None
        assert "profile" in mock_credential.scopes

        # 12. Flush called, COMMIT NOT CALLED inside service
        mock_db.flush.assert_awaited_once()
        mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_existing_refresh_token_preserved_when_provider_omits_replacement(
    provider_service, mock_db, mock_provider_auth
):
    """6. Test existing refresh token preserved when provider returns None/empty refresh token."""
    identity_id = uuid.uuid4()
    mock_credential = IdentityProviderCredential(
        id=uuid.uuid4(),
        identity_id=identity_id,
        encrypted_access_token=b"enc-old-access",
        encrypted_refresh_token=b"enc-original-refresh",
        revoked_at=None,
    )

    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="new-access-only",
        refresh_token="",  # Omitted/empty from provider
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=[],
    )

    with patch(
        "app.services.identity_provider.IdentityProviderCredentialRepository"
    ) as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_identity_id = AsyncMock(return_value=mock_credential)

        await provider_service.refresh_provider_credentials(mock_db, identity_id)

        # Original refresh token preserved
        assert mock_credential.encrypted_refresh_token == b"enc-original-refresh"
        assert mock_credential.encrypted_access_token == b"encrypted-new-access-only"


@pytest.mark.asyncio
async def test_invalid_grant_causes_soft_revocation_and_emits_event(
    provider_service, mock_db, mock_provider_auth
):
    """7 & 8. Test invalid_grant causes soft revocation and emits LOGIN_FAILED."""
    identity_id = uuid.uuid4()
    credential_id = uuid.uuid4()

    mock_credential = IdentityProviderCredential(
        id=credential_id,
        identity_id=identity_id,
        encrypted_refresh_token=b"enc-old-refresh",
        revoked_at=None,
    )

    mock_provider_auth.refresh_credentials.side_effect = ValueError(
        "invalid_grant: refresh token expired or revoked"
    )

    with patch(
        "app.services.identity_provider.IdentityProviderCredentialRepository"
    ) as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_identity_id = AsyncMock(return_value=mock_credential)
        mock_repo.revoke = AsyncMock()

        with patch("app.services.identity_provider.security_event_emitter") as mock_emitter:
            with pytest.raises(ProviderAuthError) as exc_info:
                await provider_service.refresh_provider_credentials(
                    mock_db, identity_id, request_id="req-invalid"
                )

            assert "Provider token refresh failed" in str(exc_info.value)

            # 8. Emits event with reason="refresh_failed"
            mock_emitter.emit.assert_called_once()
            event = mock_emitter.emit.call_args[0][0]
            assert event.event_type == SecurityEventType.LOGIN_FAILED
            assert event.reason == "refresh_failed"

            # 7. Soft revocation performed
            mock_repo.revoke.assert_awaited_once()
            assert mock_repo.revoke.call_args[0][0] == credential_id


@pytest.mark.asyncio
async def test_generic_provider_failure_does_not_soft_revoke(
    provider_service, mock_db, mock_provider_auth
):
    """9. Test generic provider failure (500 Error) does NOT soft-revoke credentials."""
    identity_id = uuid.uuid4()
    credential_id = uuid.uuid4()

    mock_credential = IdentityProviderCredential(
        id=credential_id,
        identity_id=identity_id,
        encrypted_refresh_token=b"enc-old-refresh",
        revoked_at=None,
    )

    mock_provider_auth.refresh_credentials.side_effect = RuntimeError(
        "500 Internal Server Error at Entra"
    )

    with patch(
        "app.services.identity_provider.IdentityProviderCredentialRepository"
    ) as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_identity_id = AsyncMock(return_value=mock_credential)
        mock_repo.revoke = AsyncMock()

        with patch("app.services.identity_provider.security_event_emitter") as mock_emitter:
            with pytest.raises(ProviderAuthError):
                await provider_service.refresh_provider_credentials(mock_db, identity_id)

            # Event emitted
            mock_emitter.emit.assert_called_once()
            event = mock_emitter.emit.call_args[0][0]
            assert event.reason == "refresh_failed"

            # Soft revoke NOT called
            mock_repo.revoke.assert_not_called()
            assert mock_credential.revoked_at is None


@pytest.mark.asyncio
async def test_device_session_and_platform_session_remain_untouched_on_failure(
    provider_service, mock_db, mock_provider_auth
):
    """10 & 11. Verify DeviceSession and platform session are untouched on provider failure."""
    identity_id = uuid.uuid4()
    mock_credential = IdentityProviderCredential(
        id=uuid.uuid4(),
        identity_id=identity_id,
        encrypted_refresh_token=b"enc-old-refresh",
        revoked_at=datetime.now(UTC),  # Already revoked credential
    )

    with patch(
        "app.services.identity_provider.IdentityProviderCredentialRepository"
    ) as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_identity_id = AsyncMock(return_value=mock_credential)

        with patch("app.auth.sessions.SessionService") as mock_session_service_cls:
            with pytest.raises(ProviderAuthError, match="Provider credentials have been revoked"):
                await provider_service.refresh_provider_credentials(mock_db, identity_id)

            # Session service never touched
            mock_session_service_cls.assert_not_called()


@pytest.mark.asyncio
async def test_credential_failure_propagates_correctly_and_allows_rollback(
    provider_service, mock_db, mock_provider_auth
):
    """13 & 14. Verify credential failure propagates clean ProviderAuthError for rollback."""
    identity_id = uuid.uuid4()
    mock_credential = IdentityProviderCredential(
        id=uuid.uuid4(),
        identity_id=identity_id,
        encrypted_refresh_token=b"enc-corrupt-refresh",
        revoked_at=None,
    )

    with patch(
        "app.services.identity_provider.IdentityProviderCredentialRepository"
    ) as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_identity_id = AsyncMock(return_value=mock_credential)
        provider_service._encryption.decrypt_string.side_effect = Exception("Decryption key error")

        with pytest.raises(ProviderAuthError, match="Failed to decrypt provider refresh token"):
            await provider_service.refresh_provider_credentials(mock_db, identity_id)

        # No flush or commit on error before refresh
        mock_db.flush.assert_not_called()
        mock_db.commit.assert_not_called()
