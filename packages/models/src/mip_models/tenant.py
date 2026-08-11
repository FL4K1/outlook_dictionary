"""Tenant model — the data isolation boundary.

Every row in every tenant-scoped table carries a tenant_id.
Every query is filtered by tenant_id. Cross-tenant data access
is architecturally impossible.

Tenants belong to Organizations. An Organization may own multiple
Tenants for regional data isolation or departmental separation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mip_models.auth import Role
    from mip_models.mail import MailAccount
    from mip_models.organization import Organization
    from mip_models.user import Membership


import uuid  # noqa: TC003

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mip_models.base import (
    Base,
    IdentityMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


class Tenant(Base, IdentityMixin, TimestampMixin, SoftDeleteMixin):
    """A data isolation boundary within an organization.

    All tenant-scoped entities reference this table via tenant_id.
    The slug is unique within the parent organization, not globally.
    """

    __tablename__ = "tenants"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_tenant_org_slug",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Parent organization",
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable tenant name",
    )

    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="URL-safe identifier, unique within the organization",
    )

    settings: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=True,
        server_default="{}",
        comment="Tenant-specific configuration (retention, feature flags)",
    )

    data_region: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Data residency region (future: controls storage location)",
    )

    # --- Relationships ---
    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="tenants",
    )

    memberships: Mapped[list[Membership]] = relationship(
        "Membership",
        back_populates="tenant",
        lazy="selectin",
    )

    mail_accounts: Mapped[list[MailAccount]] = relationship(
        "MailAccount",
        back_populates="tenant",
        lazy="selectin",
    )

    roles: Mapped[list[Role]] = relationship(
        "Role",
        back_populates="tenant",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, slug='{self.slug}')>"
