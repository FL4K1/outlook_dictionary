"""Provider authentication orchestration service.

Orchestrates the Entra ID callback flow: state validation, token exchange,
ID-token validation, identity resolution, JIT provisioning, session creation,
and credential storage.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

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
    IdentityProviderCredentialRepository,
    OAuthStateRepository,
)
from app.repositories.mail import MailAccountRepository
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

    async def refresh_provider_credentials(
        self,
        db: AsyncSession,
        identity_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        """Refresh provider credentials for an identity.

        Fetches the current credentials, decrypts the refresh token, requests a
        new token set, re-encrypts the new tokens, and persists the update.
        """
        repo = IdentityProviderCredentialRepository(db)
        credential = await repo.get_by_identity_id(identity_id)
        if not credential:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="refresh_failed",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError("No provider credentials found for identity.")

        if credential.revoked_at:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="refresh_failed",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            # Do NOT revoke DeviceSession per EDD AD-PR13-012
            raise ProviderAuthError("Provider credentials have been revoked.")

        try:
            plain_refresh_token = self._encryption.decrypt_string(
                credential.encrypted_refresh_token
            )
        except Exception as exc:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="refresh_failed",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            raise ProviderAuthError("Failed to decrypt provider refresh token.") from exc

        try:
            new_tokens = await self._provider_auth.refresh_credentials(plain_refresh_token)
        except Exception as exc:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.LOGIN_FAILED,
                    outcome=SecurityOutcome.FAILURE,
                    reason="refresh_failed",
                    metadata={"provider": "microsoft"},
                    request_id=request_id,
                )
            )
            if "invalid_grant" in str(exc).lower():
                await repo.revoke(credential.id, datetime.now(UTC))
            raise ProviderAuthError(f"Provider token refresh failed: {exc}") from exc

        credential.encrypted_access_token = self._encryption.encrypt(new_tokens.access_token)
        if new_tokens.refresh_token:
            credential.encrypted_refresh_token = self._encryption.encrypt(new_tokens.refresh_token)
        credential.token_expires_at = new_tokens.expires_at
        if new_tokens.scopes:
            credential.scopes = new_tokens.scopes
        credential.encryption_key_id = self._encryption.key_id

        await db.flush()

    async def refresh_mail_account_credentials(
        self,
        db: AsyncSession,
        mail_account_id: uuid.UUID,
        worker_id: uuid.UUID,
        expected_generation: int | None = None,
        lease_duration: timedelta = timedelta(minutes=5),
        request_id: str | None = None,
    ) -> Any:
        """Refresh mail account provider credentials using fenced CAS primitives."""
        from mip_providers.identity.base import ProviderCredentialSet

        account_repo = MailAccountRepository(db)

        # 1. Read current account from DB
        account = await account_repo.get(mail_account_id)
        if not account:
            raise ProviderAuthError("Mail account not found.")

        # Check generation optimization before lock if expected_generation provided
        if expected_generation is not None and account.credential_generation > expected_generation:
            cred = await self._get_provider_credential_by_account(db, mail_account_id)
            if cred:
                decrypted_access = self._encryption.decrypt_string(cred.encrypted_access_token)
                decrypted_refresh = self._encryption.decrypt_string(cred.encrypted_refresh_token)
                return ProviderCredentialSet(
                    access_token=decrypted_access,
                    refresh_token=decrypted_refresh,
                    expires_at=cred.token_expires_at,
                    scopes=cred.scopes or [],
                )

        # 2. Acquire refresh lease via CAS
        lease_info = await account_repo.acquire_refresh_lease(
            mail_account_id, worker_id, lease_duration
        )
        if lease_info is None:
            # Active lease owned by another worker
            reloaded_account = await account_repo.get(mail_account_id)
            if (
                reloaded_account
                and expected_generation is not None
                and reloaded_account.credential_generation > expected_generation
            ):
                cred = await self._get_provider_credential_by_account(db, mail_account_id)
                if cred:
                    decrypted_access = self._encryption.decrypt_string(cred.encrypted_access_token)
                    decrypted_refresh = self._encryption.decrypt_string(
                        cred.encrypted_refresh_token
                    )
                    return ProviderCredentialSet(
                        access_token=decrypted_access,
                        refresh_token=decrypted_refresh,
                        expires_at=cred.token_expires_at,
                        scopes=cred.scopes or [],
                    )
            raise ProviderAuthError("Active refresh lease owned by another worker.")

        lease_version, current_gen = lease_info

        # Re-check generation after lease acquisition
        if expected_generation is not None and current_gen > expected_generation:
            await account_repo.release_refresh_lease(mail_account_id, worker_id, lease_version)
            await db.commit()
            cred = await self._get_provider_credential_by_account(db, mail_account_id)
            if cred:
                decrypted_access = self._encryption.decrypt_string(cred.encrypted_access_token)
                decrypted_refresh = self._encryption.decrypt_string(cred.encrypted_refresh_token)
                return ProviderCredentialSet(
                    access_token=decrypted_access,
                    refresh_token=decrypted_refresh,
                    expires_at=cred.token_expires_at,
                    scopes=cred.scopes or [],
                )

        cred = await self._get_provider_credential_by_account(db, mail_account_id)
        if not cred:
            await account_repo.release_refresh_lease(mail_account_id, worker_id, lease_version)
            await db.commit()
            raise ProviderAuthError("Provider credentials not found for mail account.")

        # 3. Decrypt refresh token
        try:
            plain_refresh_token = self._encryption.decrypt_string(cred.encrypted_refresh_token)
        except Exception as exc:
            await account_repo.release_refresh_lease(mail_account_id, worker_id, lease_version)
            await db.commit()
            raise ProviderAuthError(f"Failed to decrypt refresh token: {exc}") from exc

        # 4. Call provider refresh API outside DB transaction
        await db.commit()
        try:
            new_tokens = await self._provider_auth.refresh_credentials(plain_refresh_token)
        except Exception as exc:
            await account_repo.release_refresh_lease(mail_account_id, worker_id, lease_version)
            await db.commit()
            err_str = str(exc).lower()
            if "invalid_grant" in err_str or "revoked" in err_str or "expired" in err_str:
                account.status = "reauth_required"
                await db.commit()
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.LOGIN_FAILED,
                        outcome=SecurityOutcome.FAILURE,
                        reason="refresh_failed",
                        metadata={"provider": account.provider_type},
                        request_id=request_id,
                    )
                )
            raise ProviderAuthError(f"Provider token refresh failed: {exc}") from exc

        # 5. Encrypt new credential material
        encrypted_access = self._encryption.encrypt(new_tokens.access_token)
        encrypted_refresh = (
            self._encryption.encrypt(new_tokens.refresh_token)
            if new_tokens.refresh_token
            else cred.encrypted_refresh_token
        )

        # 6. Revalidate via CAS fencing
        cas_ok = await account_repo.finalize_refresh_lease(
            mail_account_id, worker_id, lease_version
        )
        if not cas_ok:
            raise ProviderAuthError("Stale worker refresh CAS failed.")

        # Atomically update ProviderCredential in the same transaction
        cred.encrypted_access_token = encrypted_access
        if new_tokens.refresh_token:
            cred.encrypted_refresh_token = encrypted_refresh
        cred.token_expires_at = new_tokens.expires_at
        if new_tokens.scopes:
            cred.scopes = new_tokens.scopes
        cred.encryption_key_id = self._encryption.key_id

        await db.commit()
        return new_tokens

    async def _get_provider_credential_by_account(
        self, db: AsyncSession, mail_account_id: uuid.UUID
    ) -> Any:
        from sqlalchemy import select

        from mip_models.mail import ProviderCredential

        result = await db.execute(
            select(ProviderCredential).where(ProviderCredential.mail_account_id == mail_account_id)
        )
        return result.scalars().first()

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
