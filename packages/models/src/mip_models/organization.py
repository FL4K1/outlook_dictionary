"""Organization model — the top-level billing entity.

Organizations own Tenants and represent companies, departments, or teams.
Billing and plan management happen at the Organization level.
Data isolation happens at the Tenant level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mip_models.tenant import Tenant


from datetime import datetime  # noqa: TC003

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mip_models.base import (
    Base,
    IdentityMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


class Organization(Base, IdentityMixin, TimestampMixin, SoftDeleteMixin):
    """An organization using the platform.

    This is the root billing entity. Each organization may own multiple
    tenants for data isolation between departments, regions, or projects.
    """

    __tablename__ = "organizations"

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
        comment="URL-safe unique identifier for the organization",
    )

    billing_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        comment="Primary billing contact email",
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
        comment="Organization-level feature flags and billing metadata",
    )

    domain: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Verified email domain for JIT provisioning",
    )

    domain_verified_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="When the domain was verified via DNS TXT record",
    )

    # --- Relationships (string refs to avoid circular imports) ---
    tenants: Mapped[list[Tenant]] = relationship(
        "Tenant",
        back_populates="organization",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, slug='{self.slug}', plan='{self.plan_tier}')>"
