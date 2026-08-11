"""Mail account and provider credential models.

Mail accounts bridge the platform and an external mail provider.
Provider credentials store encrypted OAuth tokens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mip_models.tenant import Tenant


import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mip_models.base import (
    Base,
    IdentityMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


class MailAccount(Base, IdentityMixin, TimestampMixin, SoftDeleteMixin):
    """A connected mailbox."""

    __tablename__ = "mail_accounts"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "email_address",
            name="uq_mailaccount_email_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="microsoft_graph, gmail, imap",
    )

    email_address: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    account_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="user, shared, service",
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="active, syncing, error, disconnected, suspended",
    )

    connected_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    connected_at: Mapped[datetime] = mapped_column(
        nullable=False,
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    sync_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    settings: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=True,
        server_default="{}",
    )

    # --- Relationships ---
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="mail_accounts",
    )

    provider_credential: Mapped[ProviderCredential] = relationship(
        "ProviderCredential",
        back_populates="mail_account",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<MailAccount(id={self.id}, email='{self.email_address}')>"


class ProviderCredential(Base, IdentityMixin, TimestampMixin):
    """Encrypted OAuth tokens for mail provider access."""

    __tablename__ = "provider_credentials"

    mail_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Denormalized for query safety",
    )

    encrypted_access_token: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
        comment="AES-256-GCM encrypted",
    )

    encrypted_refresh_token: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
        comment="AES-256-GCM encrypted",
    )

    token_expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
    )

    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String),
        nullable=True,
    )

    encryption_key_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Identifier of the DEK used for encryption",
    )

    # --- Relationships ---
    mail_account: Mapped[MailAccount] = relationship(
        "MailAccount",
        back_populates="provider_credential",
    )

    def __repr__(self) -> str:
        return f"<ProviderCredential(id={self.id}, account={self.mail_account_id})>"
