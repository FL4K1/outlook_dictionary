"""Authorization and Session models.

Roles and Permissions define RBAC.
ServiceAccounts and ApiKeys define programmatic access.
Sessions define user login states.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mip_models.tenant import Tenant
    from mip_models.user import Membership, User


import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mip_models.base import (
    Base,
    IdentityMixin,
    TimestampMixin,
)


class Role(Base, IdentityMixin, TimestampMixin):
    """A collection of permissions."""

    __tablename__ = "roles"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Null for system roles, populated for custom tenant roles",
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="System roles cannot be deleted or modified",
    )

    # --- Relationships ---
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="roles",
    )

    memberships: Mapped[list[Membership]] = relationship(
        "Membership",
        back_populates="role",
    )

    permissions: Mapped[list[Permission]] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
        lazy="selectin",
    )

    service_accounts: Mapped[list[ServiceAccount]] = relationship(
        "ServiceAccount",
        back_populates="role",
    )

    def __repr__(self) -> str:
        return f"<Role(id={self.id}, name='{self.name}')>"


class Permission(Base, IdentityMixin):
    """A granular authorization right."""

    __tablename__ = "permissions"

    codename: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Unique identifier, e.g., mail_account.connect",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="e.g., mail_account, search, tenant",
    )

    # --- Relationships ---
    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary="role_permissions",
        back_populates="permissions",
    )

    def __repr__(self) -> str:
        return f"<Permission(id={self.id}, codename='{self.codename}')>"


class RolePermission(Base):
    """Join table between Role and Permission."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )

    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Session(Base, IdentityMixin, TimestampMixin):
    """A user login session."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    refresh_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hash of the refresh token",
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
    )

    last_active_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # --- Relationships ---
    user: Mapped[User] = relationship(
        "User",
        back_populates="sessions",
    )

    def __repr__(self) -> str:
        return f"<Session(id={self.id}, user={self.user_id})>"


class DeviceSession(Base, IdentityMixin, TimestampMixin):
    """A stable, user-visible authentication session tied to a specific device."""

    __tablename__ = "device_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    current_refresh_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hash of the current refresh token",
    )

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    user: Mapped[User] = relationship(
        "User",
        back_populates="device_sessions",
    )

    refresh_token_families: Mapped[list[RefreshTokenFamily]] = relationship(
        back_populates="device_session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<DeviceSession(id={self.id}, user={self.user_id})>"


class RefreshTokenFamily(Base, IdentityMixin, TimestampMixin):
    """A single refresh-token epoch within a device session family."""

    __tablename__ = "refresh_token_families"

    device_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hash of the refresh token",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this refresh token was rotated/consumed",
    )

    device_session: Mapped[DeviceSession] = relationship(
        back_populates="refresh_token_families",
    )

    def __repr__(self) -> str:
        return f"<RefreshTokenFamily(id={self.id}, device_session={self.device_session_id})>"


class ServiceAccount(Base, IdentityMixin, TimestampMixin):
    """A non-human programmatic identity."""

    __tablename__ = "service_accounts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # --- Relationships ---
    role: Mapped[Role] = relationship(
        "Role",
        back_populates="service_accounts",
    )

    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey",
        back_populates="service_account",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ServiceAccount(id={self.id}, name='{self.name}')>"


class ApiKey(Base, IdentityMixin, TimestampMixin):
    """An authentication credential for a ServiceAccount."""

    __tablename__ = "api_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    service_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )

    key_prefix: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
    )

    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String),
        nullable=True,
        comment="Subset of permissions granted",
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # --- Relationships ---
    service_account: Mapped[ServiceAccount] = relationship(
        "ServiceAccount",
        back_populates="api_keys",
    )

    def __repr__(self) -> str:
        return f"<ApiKey(id={self.id}, prefix='{self.key_prefix}')>"
