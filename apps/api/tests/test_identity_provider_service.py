"""Unit tests for ProviderAuthService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import AuthenticationResult
from app.common.config import Settings
from app.repositories.core import TenantRepository
from app.repositories.identity_provider import EntraTenantMappingRepository, OAuthStateRepository
from app.services.identity_provider import ProviderAuthError, ProviderAuthService
from mip_models.identity_provider import EntraTenantMapping, OAuthState
from mip_models.tenant import Tenant
from mip_models.user import Identity, Membership
from mip_providers.identity.base import IdentityVerificationResult
from mip_providers.identity.entra import EntraIdentityProviderAuth

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock(spec=AsyncSession)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        app_env="testing",
        entra_client_id="test-client-id",
        entra_tenant_id="entra-tenant-123",
        entra_redirect_uri="http://localhost/callback",
        entra_scopes=["openid", "profile", "email"],
    )


@pytest.fixture
def mock_provider_auth() -> MagicMock:
    auth = MagicMock(spec=EntraIdentityProviderAuth)
    auth.validate_callback = AsyncMock()
    auth.get_authorization_url = AsyncMock(
        return_value="https://login.microsoftonline.com/tenant-id/oauth2/v2.0/authorize?state=abc"
    )
    return auth


@pytest.fixture
def service(mock_provider_auth: MagicMock, mock_settings: Settings) -> ProviderAuthService:
    encryption = MagicMock()
    encryption.encrypt = MagicMock(return_value=b"encrypted")
    encryption.key_id = "dek-v1"
    return ProviderAuthService(
        provider_auth=mock_provider_auth,
        settings=mock_settings,
        encryption_service=encryption,
    )


def _setup_execute_result(mock_db: MagicMock, result: object) -> None:
    mock_result = MagicMock()
    if isinstance(result, list):
        mock_result.scalars.return_value.all.return_value = result
        mock_result.scalars.return_value.first.return_value = result[0] if result else None
    else:
        mock_result.scalars.return_value.first.return_value = result
        mock_result.scalars.return_value.all.return_value = [result] if result else []
    mock_db.execute.return_value = mock_result


# ---------------------------------------------------------------------------
# initiate_login tests
# ---------------------------------------------------------------------------


class TestInitiateLogin:
    """Tests for ProviderAuthService.initiate_login."""

    async def test_initiate_login_creates_state(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        state_repo.create_state = AsyncMock()
        mock_db.commit = AsyncMock()

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )

        before = datetime.now(UTC)
        url = await service.initiate_login(mock_db, request_id="req-1")
        after = datetime.now(UTC)

        assert "login.microsoftonline.com" in url
        assert "state=" in url
        state_repo.create_state.assert_called_once()
        mock_db.commit.assert_called_once()

        # S-004: verify the state expires_at is in the future (~10 minutes).
        # This assertion fails if expires_at=datetime.now(UTC) (immediate expiry).
        call_kwargs = state_repo.create_state.call_args.kwargs
        expires_at = call_kwargs["expires_at"]
        tolerance = timedelta(seconds=60)
        assert expires_at > after, "expires_at must be in the future"
        assert expires_at <= before + timedelta(minutes=10) + tolerance, (
            "expires_at must not exceed 10 minutes + tolerance from call time"
        )


# ---------------------------------------------------------------------------
# handle_callback tests
# ---------------------------------------------------------------------------


class TestHandleCallback:
    """Tests for ProviderAuthService.handle_callback."""

    async def test_handle_callback_invalid_state(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        state_repo.consume_state = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(first=AsyncMock(return_value=None)))
            )
        )

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )
        with pytest.raises(ProviderAuthError, match="Invalid or expired state"):
            await service.handle_callback(
                mock_db, code="code", state="bad-state", request_id="req-1"
            )

    async def test_handle_callback_token_validation_failure(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        mock_provider_auth: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        oauth_state = OAuthState(
            id=uuid.uuid4(),
            state="valid-state",
            nonce="valid-nonce",
            code_verifier="verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        state_repo.consume_state = AsyncMock(return_value=oauth_state)
        mock_provider_auth.validate_callback = AsyncMock(side_effect=ValueError("Invalid token"))

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )
        with pytest.raises(ProviderAuthError, match="Token validation failed"):
            await service.handle_callback(
                mock_db, code="code", state="valid-state", request_id="req-1"
            )

    async def test_handle_callback_unknown_tenant(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        mock_provider_auth: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        oauth_state = OAuthState(
            id=uuid.uuid4(),
            state="valid-state",
            nonce="valid-nonce",
            code_verifier="verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        state_repo.consume_state = AsyncMock(return_value=oauth_state)
        mock_provider_auth.validate_callback = AsyncMock(
            return_value=IdentityVerificationResult(
                provider_user_id="user-sub",
                provider_email="user@example.com",
                provider_metadata={"tid": "unknown-entra-tenant", "name": "Test User"},
            )
        )

        mapping_repo = MagicMock(spec=EntraTenantMappingRepository)
        mapping_repo.get_by_entra_tenant_id = AsyncMock(return_value=None)

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.EntraTenantMappingRepository",
            lambda db: mapping_repo,
        )
        with pytest.raises(ProviderAuthError, match="Unknown Entra tenant"):
            await service.handle_callback(
                mock_db, code="code", state="valid-state", request_id="req-1"
            )

    async def test_handle_callback_inactive_tenant(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        mock_provider_auth: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        oauth_state = OAuthState(
            id=uuid.uuid4(),
            state="valid-state",
            nonce="valid-nonce",
            code_verifier="verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        state_repo.consume_state = AsyncMock(return_value=oauth_state)
        mock_provider_auth.validate_callback = AsyncMock(
            return_value=IdentityVerificationResult(
                provider_user_id="user-sub",
                provider_email="user@example.com",
                provider_metadata={"tid": "entra-tenant-123", "name": "Test User"},
            )
        )

        mapping = EntraTenantMapping(
            id=uuid.uuid4(),
            entra_tenant_id="entra-tenant-123",
            tenant_id=uuid.uuid4(),
            is_active=True,
        )
        mapping_repo = MagicMock(spec=EntraTenantMappingRepository)
        mapping_repo.get_by_entra_tenant_id = AsyncMock(return_value=mapping)

        tenant = Tenant(
            id=mapping.tenant_id,
            organization_id=uuid.uuid4(),
            name="Test Tenant",
            slug="test-tenant",
        )
        tenant.is_active = False
        tenant_repo = MagicMock(spec=TenantRepository)
        tenant_repo.get = AsyncMock(return_value=tenant)

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.EntraTenantMappingRepository",
            lambda db: mapping_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.TenantRepository",
            lambda db: tenant_repo,
        )
        with pytest.raises(ProviderAuthError, match="Tenant not found or inactive"):
            await service.handle_callback(
                mock_db, code="code", state="valid-state", request_id="req-1"
            )


class TestHandleCallbackAtomicity:
    """Tests for F-003: handle_callback transaction atomicity."""

    async def test_handle_callback_does_not_commit_internally(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        mock_provider_auth: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        oauth_state = OAuthState(
            id=uuid.uuid4(),
            state="valid-state",
            nonce="valid-nonce",
            code_verifier="verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        state_repo.consume_state = AsyncMock(return_value=oauth_state)

        mock_provider_auth.validate_callback = AsyncMock(
            return_value=IdentityVerificationResult(
                provider_user_id="user-sub",
                provider_email="user@example.com",
                provider_metadata={"tid": "entra-tenant-123", "name": "Test User"},
                access_token="access-token",
                refresh_token="refresh-token",
                token_expires_at=datetime.now(UTC),
                scopes=["openid"],
            )
        )

        mapping = EntraTenantMapping(
            id=uuid.uuid4(),
            entra_tenant_id="entra-tenant-123",
            tenant_id=uuid.uuid4(),
            is_active=True,
        )
        mapping_repo = MagicMock(spec=EntraTenantMappingRepository)
        mapping_repo.get_by_entra_tenant_id = AsyncMock(return_value=mapping)

        tenant = Tenant(
            id=mapping.tenant_id,
            organization_id=uuid.uuid4(),
            name="Test Tenant",
            slug="test-tenant",
            is_active=True,
        )
        tenant_repo = MagicMock(spec=TenantRepository)
        tenant_repo.get = AsyncMock(return_value=tenant)

        role_repo = MagicMock()
        default_role = MagicMock()
        default_role.id = uuid.uuid4()
        role_repo.get_system_role = AsyncMock(return_value=default_role)

        identity = MagicMock(spec=Identity)
        identity.id = uuid.uuid4()
        identity.user_id = uuid.uuid4()
        identity.provider = "microsoft"
        identity.user.tenant_id = tenant.id

        membership = MagicMock(spec=Membership)
        membership.id = uuid.uuid4()
        membership.role_id = default_role.id
        membership.is_active = True

        session = MagicMock()
        session.id = uuid.uuid4()
        session.identity_id = None

        def _setup_execute(result):
            mock_result = MagicMock()
            mock_result.scalars.return_value.first.return_value = result
            return mock_result

        mock_db.execute = AsyncMock(
            side_effect=[
                _setup_execute(identity),
                _setup_execute(membership),
                _setup_execute(session),
            ]
        )

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.EntraTenantMappingRepository",
            lambda db: mapping_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.TenantRepository",
            lambda db: tenant_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.RoleRepository",
            lambda db: role_repo,
        )

        result = await service.handle_callback(
            mock_db, code="code", state="valid-state", request_id="req-1"
        )

        assert isinstance(result, AuthenticationResult)
        mock_db.commit.assert_not_called()

    async def test_handle_callback_rollback_on_credential_failure(
        self,
        service: ProviderAuthService,
        mock_db: MagicMock,
        mock_provider_auth: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_repo = MagicMock(spec=OAuthStateRepository)
        oauth_state = OAuthState(
            id=uuid.uuid4(),
            state="valid-state",
            nonce="valid-nonce",
            code_verifier="verifier",
            provider="microsoft",
            expires_at=datetime.now(UTC),
            consumed_at=None,
        )
        state_repo.consume_state = AsyncMock(return_value=oauth_state)

        mock_provider_auth.validate_callback = AsyncMock(
            return_value=IdentityVerificationResult(
                provider_user_id="user-sub",
                provider_email="user@example.com",
                provider_metadata={"tid": "entra-tenant-123", "name": "Test User"},
                access_token="access-token",
                refresh_token="refresh-token",
                token_expires_at=datetime.now(UTC),
                scopes=["openid"],
            )
        )

        mapping = EntraTenantMapping(
            id=uuid.uuid4(),
            entra_tenant_id="entra-tenant-123",
            tenant_id=uuid.uuid4(),
            is_active=True,
        )
        mapping_repo = MagicMock(spec=EntraTenantMappingRepository)
        mapping_repo.get_by_entra_tenant_id = AsyncMock(return_value=mapping)

        tenant = Tenant(
            id=mapping.tenant_id,
            organization_id=uuid.uuid4(),
            name="Test Tenant",
            slug="test-tenant",
            is_active=True,
        )
        tenant_repo = MagicMock(spec=TenantRepository)
        tenant_repo.get = AsyncMock(return_value=tenant)

        role_repo = MagicMock()
        default_role = MagicMock()
        default_role.id = uuid.uuid4()
        role_repo.get_system_role = AsyncMock(return_value=default_role)

        identity = MagicMock(spec=Identity)
        identity.id = uuid.uuid4()
        identity.user_id = uuid.uuid4()
        identity.provider = "microsoft"
        identity.user.tenant_id = tenant.id

        membership = MagicMock(spec=Membership)
        membership.id = uuid.uuid4()
        membership.role_id = default_role.id
        membership.is_active = True

        session = MagicMock()
        session.id = uuid.uuid4()
        session.identity_id = None

        def _setup_execute(result):
            mock_result = MagicMock()
            mock_result.scalars.return_value.first.return_value = result
            return mock_result

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _setup_execute(identity)
            elif call_count == 2:
                return _setup_execute(membership)
            elif call_count == 3:
                return _setup_execute(session)
            return _setup_execute(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        flush_count = 0

        async def flush_side_effect(*args, **kwargs):
            nonlocal flush_count
            flush_count += 1
            if flush_count <= 2:
                return None
            raise RuntimeError("Credential storage failed")

        mock_db.flush = AsyncMock(side_effect=flush_side_effect)

        monkeypatch.setattr(
            "app.services.identity_provider.OAuthStateRepository",
            lambda db: state_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.EntraTenantMappingRepository",
            lambda db: mapping_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.TenantRepository",
            lambda db: tenant_repo,
        )
        monkeypatch.setattr(
            "app.services.identity_provider.RoleRepository",
            lambda db: role_repo,
        )

        with pytest.raises(RuntimeError, match="Credential storage failed"):
            await service.handle_callback(
                mock_db, code="code", state="valid-state", request_id="req-1"
            )


# ---------------------------------------------------------------------------
# Router-level rollback tests (M-001)
# ---------------------------------------------------------------------------


class TestEntraCallbackRouterRollback:
    """Tests for M-001: entra_router rolls back the DB transaction on failure.

    handle_callback deliberately does not commit or rollback — those actions
    belong to the router's try/except block (entra_router.py). These tests
    exercise the router layer using an ASGI test client so the transaction
    boundary is verified at the correct architectural level.
    """

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        db = MagicMock(spec=AsyncSession)
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.flush = AsyncMock()
        return db

    @pytest.fixture
    def app(self, mock_db: MagicMock):
        from app.common.config import Environment, LogFormat, Settings
        from app.common.dependencies import get_db, get_settings
        from app.main import create_app

        settings = Settings(
            app_env=Environment.TESTING,
            app_debug=False,
            app_log_level="WARNING",
            app_log_format=LogFormat.CONSOLE,
            entra_client_id="test-client-id",
            entra_tenant_id="test-tenant-id",
            entra_redirect_uri="http://localhost/callback",
            entra_scopes=["openid"],
        )
        _app = create_app(settings=settings)

        async def _override_get_db():
            yield mock_db

        def _override_get_settings():
            return settings

        _app.dependency_overrides[get_db] = _override_get_db
        _app.dependency_overrides[get_settings] = _override_get_settings
        return _app

    async def test_callback_rollback_on_service_failure(
        self,
        app,
        mock_db: MagicMock,
    ) -> None:
        """Router calls db.rollback() when handle_callback raises.

        Verifies the M-001 gap: the router's except clause must rollback
        the transaction when ProviderAuthService.handle_callback fails.
        """
        from httpx import ASGITransport, AsyncClient

        from app.services.identity_provider import ProviderAuthError

        # Patch the factory that creates the provider auth service so we avoid
        # EncryptionService/DEK initialisation while still exercising the router's
        # commit/rollback boundary.
        mock_service = MagicMock()
        mock_service.handle_callback = AsyncMock(
            side_effect=ProviderAuthError("Simulated credential failure")
        )
        with patch(
            "app.api.auth.entra_router._get_provider_auth_service",
            return_value=mock_service,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/auth/callback/entra",
                    json={"code": "test-code", "state": "test-state"},
                )

        assert response.status_code in {400, 401, 422, 500}
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

    async def test_callback_commits_on_success(
        self,
        app,
        mock_db: MagicMock,
    ) -> None:
        """Router calls db.commit() (not rollback) when handle_callback succeeds."""
        from httpx import ASGITransport, AsyncClient

        from app.auth.service import AuthenticationResult

        fake_result = AuthenticationResult(
            access_token="platform-access-token",
            refresh_token="platform-refresh-token",
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )

        mock_service = MagicMock()
        mock_service.handle_callback = AsyncMock(return_value=fake_result)
        with patch(
            "app.api.auth.entra_router._get_provider_auth_service",
            return_value=mock_service,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/auth/callback/entra",
                    json={"code": "test-code", "state": "test-state"},
                )

        assert response.status_code == 200
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()
