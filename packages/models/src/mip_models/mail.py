"""Mail account, provider credential, and mail synchronization models.

Mail accounts bridge the platform and an external mail provider.
Provider credentials store encrypted OAuth tokens.
Mail folders, messages, sync states, and outbox events store sync data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mip_models.tenant import Tenant


import uuid  # noqa: TC003
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mip_models.base import (
    ARRAYType,
    Base,
    BYTEAType,
    IdentityMixin,
    SoftDeleteMixin,
    TimestampMixin,
)

# Cross-dialect JSON column type (JSONB on Postgres, JSON on SQLite)
JSONType = JSON().with_variant(JSONB, "postgresql")


class MailSyncStateValue(StrEnum):
    """Supported values for MailSyncState.state."""

    PENDING_INITIAL_SYNC = "PENDING_INITIAL_SYNC"
    SYNCING = "SYNCING"
    DELTA_TRACKING = "DELTA_TRACKING"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ERROR = "ERROR"


class OutboxEventStatus(StrEnum):
    """Supported values for OutboxEvent.status."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DONE = "DONE"
    DEAD_LETTER = "DEAD_LETTER"


class ParticipantRole(StrEnum):
    """Supported participant roles."""

    TO = "TO"
    CC = "CC"
    BCC = "BCC"


class MailAccount(Base, IdentityMixin, TimestampMixin, SoftDeleteMixin):
    """A connected mailbox."""

    __tablename__ = "mail_accounts"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "email_address",
            name="uq_mailaccount_email_tenant",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_mail_accounts_id_tenant_id",
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
        DateTime(timezone=True),
        nullable=False,
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sync_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    credential_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
    )

    refresh_locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        default=None,
    )

    refresh_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    refresh_lease_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
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
        BYTEAType,
        nullable=False,
        comment="AES-256-GCM encrypted",
    )

    encrypted_refresh_token: Mapped[bytes] = mapped_column(
        BYTEAType,
        nullable=False,
        comment="AES-256-GCM encrypted",
    )

    token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAYType,
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


class MailFolder(Base, IdentityMixin):
    """A mail folder within a connected mailbox."""

    __tablename__ = "mail_folders"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "tenant_id",
            "mail_account_id",
            name="uq_mail_folders_id_tenant_account",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_mail_folders_id_tenant_id",
        ),
        UniqueConstraint(
            "mail_account_id",
            "provider_folder_id",
            name="uq_mail_folder_prov",
        ),
        ForeignKeyConstraint(
            ["mail_account_id", "tenant_id"],
            ["mail_accounts.id", "mail_accounts.tenant_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_id", "tenant_id", "mail_account_id"],
            ["mail_folders.id", "mail_folders.tenant_id", "mail_folders.mail_account_id"],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    mail_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    provider_folder_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        default=True,
    )

    def __repr__(self) -> str:
        return f"<MailFolder(id={self.id}, name='{self.name}')>"


class MailSyncState(Base, IdentityMixin):
    """Synchronization state tracking per folder."""

    __tablename__ = "mail_sync_states"

    __table_args__ = (
        UniqueConstraint("mail_folder_id", name="uq_mail_sync_states_folder_id"),
        ForeignKeyConstraint(
            ["mail_folder_id", "tenant_id"],
            ["mail_folders.id", "mail_folders.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    mail_folder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )

    state: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    sync_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    resync_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
    )

    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lease_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<MailSyncState(id={self.id}, folder_id={self.mail_folder_id}, state='{self.state}')>"
        )


class MailMessage(Base, IdentityMixin):
    """A synchronized mail message."""

    __tablename__ = "mail_messages"

    __table_args__ = (
        UniqueConstraint(
            "id",
            "tenant_id",
            "mail_account_id",
            name="uq_mail_messages_id_tenant_account",
        ),
        UniqueConstraint(
            "mail_account_id",
            "provider_message_id",
            name="uq_mail_message_prov",
        ),
        ForeignKeyConstraint(
            ["mail_account_id", "tenant_id"],
            ["mail_accounts.id", "mail_accounts.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    mail_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    provider_message_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="1",
        default=1,
    )

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
    )

    subject: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
    )

    body_preview: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sender: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
    )

    received_date_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    has_attachments: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
    )

    def __repr__(self) -> str:
        return f"<MailMessage(id={self.id}, prov_id='{self.provider_message_id}')>"


class MailMessageFolder(Base):
    """Pivot table mapping mail messages to folders."""

    __tablename__ = "mail_message_folders"

    __table_args__ = (
        PrimaryKeyConstraint(
            "mail_message_id",
            "mail_folder_id",
            name="pk_mail_message_folders",
        ),
        ForeignKeyConstraint(
            ["mail_message_id", "tenant_id", "mail_account_id"],
            ["mail_messages.id", "mail_messages.tenant_id", "mail_messages.mail_account_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["mail_folder_id", "tenant_id", "mail_account_id"],
            ["mail_folders.id", "mail_folders.tenant_id", "mail_folders.mail_account_id"],
            ondelete="CASCADE",
        ),
        Index("idx_mail_message_folders_resync", "mail_folder_id", "resync_generation"),
    )

    mail_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    mail_folder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    mail_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    resync_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<MailMessageFolder(message={self.mail_message_id}, folder={self.mail_folder_id})>"


class MailMessageParticipant(Base, IdentityMixin):
    """Participants (To, Cc, Bcc) on a mail message."""

    __tablename__ = "mail_message_participants"

    __table_args__ = (
        CheckConstraint(
            "role IN ('TO', 'CC', 'BCC')",
            name="ck_mail_message_participants_role",
        ),
        ForeignKeyConstraint(
            ["mail_message_id"],
            ["mail_messages.id"],
            ondelete="CASCADE",
        ),
        Index("idx_mail_message_participants_msg", "mail_message_id"),
    )

    mail_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<MailMessageParticipant(id={self.id}, role='{self.role}', email='{self.email}')>"


class OutboxEvent(Base, IdentityMixin):
    """Transactional outbox event for asynchronous processing."""

    __tablename__ = "outbox_events"

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_events_idempotency_key"),
        Index(
            "idx_outbox_events_pending",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'IN_FLIGHT')"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    aggregate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    aggregate_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONType,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="PENDING",
        default=OutboxEventStatus.PENDING,
    )

    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lease_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="1",
        default=1,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        default=0,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<OutboxEvent(id={self.id}, type='{self.event_type}', status='{self.status}')>"
