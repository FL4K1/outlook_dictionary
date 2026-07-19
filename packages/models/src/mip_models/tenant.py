"""Tenant model — the root entity of the platform's multi-tenant data model.

Every organization using the platform is represented by a Tenant.
All other tenant-scoped entities reference this table via tenant_id.
"""

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mip_models.base import Base, IdentityMixin, TimestampMixin


class Tenant(Base, IdentityMixin, TimestampMixin):
    """An organization using the platform.

    This is the first and only production table created in Milestone 0.
    All subsequent models will reference tenant_id as a foreign key.
    """

    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable organization name",
    )

    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
        comment="URL-safe unique identifier for the tenant",
    )

    plan_tier: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="free",
        comment="Subscription tier: free, starter, professional, enterprise",
    )

    settings: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=True,
        server_default="{}",
        comment="Tenant-specific configuration (retention policies, feature flags, etc.)",
    )

    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default="true",
        comment="Soft-delete flag. Inactive tenants cannot access the platform.",
    )

    contact_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        comment="Primary contact email for the organization",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Internal notes about this tenant (admin use only)",
    )

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, slug='{self.slug}', plan='{self.plan_tier}')>"
