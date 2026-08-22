"""Provider authentication orchestration service.

Orchestrates the Entra ID callback flow: state validation, token exchange,
ID-token validation, identity resolution, JIT provisioning, session creation,
and credential storage.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from app.auth.events import (
    SecurityEvent,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.service import AuthenticationResult, AuthenticationService
from app.auth.sessions import SessionService
from app.auth.tokens import TokenService
from app.repositories.auth import DeviceSessionRepository, RefreshTokenFamilyRepository
from app.repositories.core import RoleRepository, TenantRepository
from app.repositories.identity_provider import (
    EntraTenantMappingRepository,
    OAuthStateRepository,
)
from mip_models.base import SystemRole
from mip_models.identity_provider import IdentityProviderCredential
from mip_models.user import Identity, Membership, User

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.config import Settings
    from app.common.encryption import EncryptionService
    from mip_models.tenant import Tenant
    from mip_providers.identity.base import (
        IdentityProviderAuth,
        IdentityVerificationResult,
    )


class ProviderAuthError(Exception):
    """Base exception for provider authentication failures."""


class TenantResolutionError(ProviderAuthError):
    """Tenant resolution failed."""


class IdentityResolutionError(ProviderAuthError):
    """Identity resolution failed."""


class ProviderAuthService:
    """Orchestrates Entra ID provider authentication flow."""

    def __init__(
        self,
        provider_auth: IdentityProviderAuth,
        settings: Settings,
        encryption_service: EncryptionService,
    ) -> None:
        self._provider_auth = provider_auth
        self._settings = settings
        self._encryption = encryption_service

    async def initiate_login(self, db: AsyncSession, request_id: str | None = None) -> str:
        """Initiate Entra ID login flow.

        Returns the Entra ID authorization URL for redirect.
        """
        state = self._generate_state()
        nonce = self._generate_nonce()
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)

        state_repo = OAuthStateRepository(db)
        await state_repo.create_state(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            provider="microsoft",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            request_id=request_id,
        )
        await db.commit()

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.LOGIN_STARTED,
                outcome=SecurityOutcome.SUCCESS,
                reason="Entra ID authorization initiated",
                metadata={"provider": "microsoft"},
                request_id=request_id,
            )
        )

        redirect_uri = self._settings.entra_redirect_uri
        return await self._provider_auth.get_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
        )

    async def handle_callback(
        self,
        db: AsyncSession,
        code: str,
        state: str,
        request_id: str | None = None,
    ) -> AuthenticationResult:
        """Handle Entra ID callback and create platform session."""
        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.CALLBACK_RECEIVED,
                outcome=SecurityOutcome.SUCCESS,
                reason="Entra ID callback received",
                metadata={"provider": "microsoft"},
                request_id=request_id,
            )
        )

        state_repo = OAuthStateRepository(db)
        oauth_state = await state_repo.consume_state(state)
        if oauth_state is None:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CALLBACK_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="invalid_state",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Invalid or expired state",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError("Invalid or expired state.")

        try:
            verification = await self._provider_auth.validate_callback(
                code=code,
                state=state,
                expected_state=oauth_state.state,
                expected_nonce=oauth_state.nonce,
                code_verifier=oauth_state.code_verifier,
            )
        except Exception as exc:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CALLBACK_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason=f"invalid_token: {exc}",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason=f"Token validation failed: {exc}",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError(f"Token validation failed: {exc}") from exc

        entra_tenant_id = verification.provider_metadata.get("tid")
        if not entra_tenant_id or not isinstance(entra_tenant_id, str):
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CALLBACK_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="tenant_resolution_failed",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Missing Entra tenant ID in token",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError("Missing Entra tenant ID in token.")

        mapping_repo = EntraTenantMappingRepository(db)
        mapping = await mapping_repo.get_by_entra_tenant_id(entra_tenant_id)
        if mapping is None:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CALLBACK_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="tenant_resolution_failed",
                    metadata={"provider": "microsoft", "entra_tenant_id": entra_tenant_id},
                    request_id=request_id,
                )
            )
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Unknown Entra tenant",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError("Unknown Entra tenant.")

        tenant_repo = TenantRepository(db)
        tenant = await tenant_repo.get(mapping.tenant_id)
        if tenant is None or not tenant.is_active:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CALLBACK_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="tenant_resolution_failed",
                    metadata={"provider": "microsoft", "tenant_id": str(mapping.tenant_id)},
                    request_id=request_id,
                )
            )
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Tenant not found or inactive",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError("Tenant not found or inactive.")

        identity = await self._find_identity(db, verification.provider_user_id)

        if identity is not None:
            if identity.user.tenant_id != tenant.id:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.LOGIN_FAILED,
                        outcome=SecurityOutcome.FAILURE,
                        reason="identity_already_linked",
                        metadata={"provider": "microsoft"},
                        request_id=request_id,
                    )
                )
                raise ProviderAuthError("Identity already linked to a different tenant.")

            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.IDENTITY_LINKED,
                    outcome=SecurityOutcome.SUCCESS,
                    user_id=identity.user_id,
                    tenant_id=tenant.id,
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            user = identity.user
        else:
            user = await self._jit_provision_user(db, verification, tenant)
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.USER_PROVISIONED,
                    outcome=SecurityOutcome.SUCCESS,
                    user_id=user.id,
                    tenant_id=tenant.id,
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            identity = await self._create_identity(db, user.id, verification, tenant.id)

        membership = await self._get_membership(db, user.id, tenant.id)
        if membership is None:
            raise ProviderAuthError("No active membership for user in tenant.")

        token_service = TokenService(self._settings)
        session_service = SessionService(
            device_session_repo=DeviceSessionRepository(db),
            refresh_token_family_repo=RefreshTokenFamilyRepository(db),
            token_service=token_service,
            settings=self._settings,
        )
        auth_service = AuthenticationService(
            session_service=session_service,
            token_service=token_service,
        )

        result = await auth_service.create_session_tokens(
            user_id=user.id,
            tenant_id=tenant.id,
            organization_id=tenant.organization_id,
            ip_address=None,
            user_agent=None,
            remember_me=False,
            request_id=request_id,
        )

        session_repo = DeviceSessionRepository(db)
        session = await session_repo.get(result.session_id)
        if session:
            session.identity_id = identity.id

        await self._store_credentials(db, identity.id, tenant.id, verification)

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.LOGIN_SUCCEEDED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=user.id,
                tenant_id=tenant.id,
                session_id=result.session_id,
                metadata={"provider": "microsoft"},
                request_id=request_id,
            )
        )

        return result

    async def _find_identity(self, db: AsyncSession, provider_user_id: str) -> Identity | None:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        result = await db.execute(
            select(Identity)
            .where(
                Identity.provider == "microsoft",
                Identity.provider_user_id == provider_user_id,
            )
            .options(selectinload(Identity.user))
        )
        return result.scalars().first()

    async def _create_identity(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        verification: IdentityVerificationResult,
        tenant_id: uuid.UUID,
    ) -> Identity:
        identity = Identity(
            user_id=user_id,
            provider="microsoft",
            provider_user_id=verification.provider_user_id,
            provider_email=verification.provider_email,
            provider_metadata=verification.provider_metadata,
        )
        db.add(identity)
        await db.flush()
        return identity

    async def _jit_provision_user(
        self,
        db: AsyncSession,
        verification: IdentityVerificationResult,
        tenant: Tenant,
    ) -> User:
        email = verification.provider_email or f"{verification.provider_user_id}@entra.local"
        display_name = verification.provider_metadata.get("name", "Entra User")

        user = User(
            email=email,
            display_name=display_name,
            is_platform_admin=False,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        role_repo = RoleRepository(db)
        default_role = await role_repo.get_system_role(SystemRole.MEMBER)
        if default_role is None:
            msg = "Default system role 'member' not found. JIT provisioning requires a member role."
            raise ProviderAuthError(msg)

        membership = Membership(
            user_id=user.id,
            tenant_id=tenant.id,
            role_id=default_role.id,
            is_active=True,
        )
        db.add(membership)
        await db.flush()

        return user

    async def _get_membership(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Membership | None:
        from sqlalchemy import select

        result = await db.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.tenant_id == tenant_id,
                Membership.is_active == True,  # noqa: E712
            )
        )
        return result.scalars().first()

    async def _store_credentials(
        self,
        db: AsyncSession,
        identity_id: uuid.UUID,
        tenant_id: uuid.UUID,
        verification: IdentityVerificationResult,
    ) -> None:
        if not verification.access_token or not verification.refresh_token:
            return

        encrypted_access = self._encryption.encrypt(verification.access_token)
        encrypted_refresh = self._encryption.encrypt(verification.refresh_token)

        credential = IdentityProviderCredential(
            identity_id=identity_id,
            tenant_id=tenant_id,
            provider="microsoft",
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_expires_at=verification.token_expires_at or datetime.now(UTC),
            scopes=verification.scopes,
            encryption_key_id=self._encryption.key_id,
        )
        db.add(credential)
        await db.flush()

    @staticmethod
    def _generate_state() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _generate_nonce() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _generate_code_verifier() -> str:
        return secrets.token_urlsafe(43)

    @staticmethod
    def _generate_code_challenge(code_verifier: str) -> str:
        challenge = sha256(code_verifier.encode("ascii")).digest()
        import base64

        return base64.urlsafe_b64encode(challenge).decode("ascii").rstrip("=")
