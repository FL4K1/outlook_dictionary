"""User and Identity models.

Users are human identities. They exist independently of tenants.
Identities are external authentication bindings (e.g. Microsoft Entra ID).
Memberships join Users to Tenants with a Role assignment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mip_models.auth import Role, Session
    from mip_models.tenant import Tenant



import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mip_models.base import (
    Base,
    IdentityMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


class User(Base, IdentityMixin, TimestampMixin, SoftDeleteMixin):
    """A human identity in the platform."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
        comment="Primary email, globally unique",
    )

    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Full name",
    )

    avatar_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="Profile picture URL",
    )

    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="Superadmin flag (platform operators only)",
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="Last successful login timestamp",
    )

    # --- Relationships ---
    identities: Mapped[list[Identity]] = relationship(
        "Identity",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    memberships: Mapped[list[Membership]] = relationship(
        "Membership",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="[Membership.user_id]",
    )

    sessions: Mapped[list[Session]] = relationship(
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}')>"


class Identity(Base, IdentityMixin, TimestampMixin):
    """An external authentication binding.

    Links a User to an external identity provider. A User may have
    multiple Identities (e.g., Microsoft + Google).
    """

    __tablename__ = "identities"

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_identity_provider_user",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="microsoft, google, okta, saml",
    )

    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="External IdP's unique user identifier (OID, sub claim)",
    )

    provider_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        comment="Email from the IdP (may differ from User.email)",
    )

    provider_metadata: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=True,
        server_default="{}",
        comment="IdP-specific claims (tenant ID, groups, etc.)",
    )

    # --- Relationships ---
    user: Mapped[User] = relationship(
        "User",
        back_populates="identities",
    )

    def __repr__(self) -> str:
        return f"<Identity(id={self.id}, provider='{self.provider}')>"


class Membership(Base, IdentityMixin, TimestampMixin):
    """The join entity between User and Tenant.

    Carries the Role assignment. This is the authorization boundary.
    """

    __tablename__ = "memberships"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tenant_id",
            name="uq_membership_user_tenant",
        ),
    )

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

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        comment="Membership active/revoked",
    )

    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who created the invitation",
    )

    joined_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="When the membership was activated",
    )

    # --- Relationships ---
    user: Mapped[User] = relationship(
        "User",
        back_populates="memberships",
        foreign_keys=[user_id],
    )

    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="memberships",
    )

    role: Mapped[Role] = relationship(
        "Role",
        back_populates="memberships",
    )

    def __repr__(self) -> str:
        return f"<Membership(id={self.id}, user={self.user_id}, tenant={self.tenant_id})>"
