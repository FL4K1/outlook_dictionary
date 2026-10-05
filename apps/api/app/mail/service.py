"""Service for managing the lifecycle of mail accounts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from mip_models.base import MailAccountStatus
from mip_models.identity_provider import IdentityProviderCredential
from mip_models.mail import (
    MailAccount,
    MailFolder,
    MailSyncState,
    ProviderCredential,
)
from mip_models.user import Identity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth.context import AuthenticationContext
    from app.common.config import Settings
    from app.common.encryption import EncryptionService


class MailAccountError(Exception):
    """Base exception for MailAccount operations."""


class IdentityNotLinkedError(MailAccountError):
    """No suitable Identity is linked or has provider credentials."""


class MailAccountNotFoundError(MailAccountError):
    """The requested MailAccount was not found or belongs to another tenant."""


class MailLifecycleService:
    """Manages creation, binding, and deactivation of MailAccounts."""

    def __init__(self, db: AsyncSession, settings: Settings, encryption: EncryptionService) -> None:
        self.db = db
        self.settings = settings
        self.encryption = encryption

    async def connect_account(self, context: AuthenticationContext) -> MailAccount:
        """Create or reconnect a MailAccount for the current authenticated principal."""
        # Find the Identity and its IdentityProviderCredential for this user + tenant
        stmt = (
            select(Identity, IdentityProviderCredential)
            .join(IdentityProviderCredential, IdentityProviderCredential.identity_id == Identity.id)
            .where(
                Identity.user_id == context.user_id,
                Identity.provider == "microsoft",
                IdentityProviderCredential.tenant_id == context.tenant_id,
                IdentityProviderCredential.revoked_at.is_(None),
            )
        )
        result = await self.db.execute(stmt)
        record = result.first()

        if not record:
            raise IdentityNotLinkedError("No active Microsoft identity credentials found.")

        identity: Identity = record[0]
        idp_cred: IdentityProviderCredential = record[1]

        email_address = identity.provider_email or f"{identity.provider_user_id}@entra.local"

        # Check for existing MailAccount
        existing_stmt = select(MailAccount).where(
            MailAccount.tenant_id == context.tenant_id,
            MailAccount.email_address == email_address,
        )
        existing_acc = (await self.db.execute(existing_stmt)).scalars().first()

        now = datetime.now(UTC)

        if existing_acc:
            # Reconnect existing account
            existing_acc.status = MailAccountStatus.ACTIVE
            existing_acc.connected_by = context.user_id
            existing_acc.connected_at = now
            existing_acc.is_active = True
            existing_acc.deleted_at = None
            account = existing_acc

            # Update existing ProviderCredential
            prov_cred_stmt = select(ProviderCredential).where(
                ProviderCredential.mail_account_id == account.id
            )
            prov_cred = (await self.db.execute(prov_cred_stmt)).scalars().first()
            if prov_cred:
                prov_cred.encrypted_access_token = idp_cred.encrypted_access_token
                prov_cred.encrypted_refresh_token = idp_cred.encrypted_refresh_token
                prov_cred.token_expires_at = idp_cred.token_expires_at
                prov_cred.scopes = idp_cred.scopes
                prov_cred.encryption_key_id = idp_cred.encryption_key_id
            else:
                new_cred = ProviderCredential(
                    mail_account_id=account.id,
                    tenant_id=account.tenant_id,
                    encrypted_access_token=idp_cred.encrypted_access_token,
                    encrypted_refresh_token=idp_cred.encrypted_refresh_token,
                    token_expires_at=idp_cred.token_expires_at,
                    scopes=idp_cred.scopes,
                    encryption_key_id=idp_cred.encryption_key_id,
                )
                self.db.add(new_cred)
        else:
            # Create new account natively
            display_name = (
                identity.provider_metadata.get("name", "Entra Mailbox")
                if identity.provider_metadata
                else "Entra Mailbox"
            )

            account = MailAccount(
                tenant_id=context.tenant_id,
                provider_type="microsoft_graph",
                email_address=email_address,
                display_name=display_name,
                account_type="user",
                status=MailAccountStatus.ACTIVE,
                connected_by=context.user_id,
                connected_at=now,
                is_active=True,
            )
            self.db.add(account)
            await self.db.flush()

            new_cred = ProviderCredential(
                mail_account_id=account.id,
                tenant_id=context.tenant_id,
                encrypted_access_token=idp_cred.encrypted_access_token,
                encrypted_refresh_token=idp_cred.encrypted_refresh_token,
                token_expires_at=idp_cred.token_expires_at,
                scopes=idp_cred.scopes,
                encryption_key_id=idp_cred.encryption_key_id,
            )
            self.db.add(new_cred)

        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def get_accounts(self, context: AuthenticationContext) -> list[MailAccount]:
        """List active mail accounts for the tenant."""
        stmt = select(MailAccount).where(
            MailAccount.tenant_id == context.tenant_id,
            MailAccount.is_active.is_(True),
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def deactivate_account(
        self, context: AuthenticationContext, account_id: uuid.UUID
    ) -> MailAccount:
        """Soft-deactivate an account, halting future syncs."""
        stmt = select(MailAccount).where(
            MailAccount.id == account_id,
            MailAccount.tenant_id == context.tenant_id,
        )
        account = (await self.db.execute(stmt)).scalars().first()
        if not account:
            raise MailAccountNotFoundError("Mail account not found")

        account.is_active = False
        account.deleted_at = datetime.now(UTC)
        account.status = MailAccountStatus.DISCONNECTED

        # Delete the provider credentials so they are not reused
        cred_stmt = select(ProviderCredential).where(
            ProviderCredential.mail_account_id == account.id
        )
        cred = (await self.db.execute(cred_stmt)).scalars().first()
        if cred:
            await self.db.delete(cred)

        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def enqueue_folder_sync(
        self, context: AuthenticationContext, account_id: uuid.UUID, arq_redis: Any
    ) -> int:
        """Enqueue sync jobs for all folders in the account via ARQ."""
        # Find folders
        stmt = select(MailFolder).where(
            MailFolder.tenant_id == context.tenant_id,
            MailFolder.mail_account_id == account_id,
            MailFolder.is_active.is_(True),
        )
        folders = (await self.db.execute(stmt)).scalars().all()
        if not folders:
            return 0


        enqueued = 0
        for f in folders:
            job = await arq_redis.enqueue_job("sync_mailbox_job", str(f.id), _job_id=f"sync:{f.id}")
            if job is not None:
                enqueued += 1
        return enqueued

    async def get_sync_status(
        self, context: AuthenticationContext, account_id: uuid.UUID
    ) -> list[MailSyncState]:
        """Return the sync status for all folders within the account."""
        stmt = select(MailAccount).where(
            MailAccount.id == account_id,
            MailAccount.tenant_id == context.tenant_id,
        )
        if not (await self.db.execute(stmt)).scalars().first():
            raise MailAccountNotFoundError("Mail account not found")

        stmt_state = (
            select(MailSyncState)
            .join(MailFolder, MailSyncState.mail_folder_id == MailFolder.id)
            .where(
                MailSyncState.tenant_id == context.tenant_id,
                MailFolder.mail_account_id == account_id,
            )
        )
        return list((await self.db.execute(stmt_state)).scalars().all())

    async def get_folders(
        self, context: AuthenticationContext, limit: int = 50, offset: int = 0
    ) -> tuple[list[MailFolder], int]:
        """Return paginated folders for the tenant."""
        from sqlalchemy import func

        count_stmt = select(func.count(MailFolder.id)).where(
            MailFolder.tenant_id == context.tenant_id
        )
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(MailFolder)
            .where(MailFolder.tenant_id == context.tenant_id)
            .limit(limit)
            .offset(offset)
        )
        folders = list((await self.db.execute(stmt)).scalars().all())
        return folders, total


class MessageDataService:
    """Service for retrieving message data via Elasticsearch."""

    def __init__(self, es_adapter: Any) -> None:
        self.es_adapter = es_adapter

    async def get_message(self, tenant_id: str, message_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch a canonical message by its ID bounded to the tenant."""
        index_name = f"mail_messages_{tenant_id}"
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"id": str(message_id)}},
                        {"term": {"tenant_id": tenant_id}},
                        {"term": {"is_deleted": False}},
                    ]
                }
            },
            "size": 1,
        }

        try:
            resp = await self.es_adapter.search(index_name=index_name, query=query)
            hits = resp.get("hits", {}).get("hits", [])
            if not hits:
                return None

            return hits[0].get("_source")  # type: ignore[no-any-return]
        except Exception:
            return None
