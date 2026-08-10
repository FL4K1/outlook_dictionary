"""SQLAlchemy base classes, reusable mixins, and domain enumerations.

Every ORM model in the platform inherits from Base.
Every tenant-scoped model additionally uses TenantMixin.
Soft-deletable models use SoftDeleteMixin.
All models use IdentityMixin (UUID PK) and TimestampMixin (created/updated).
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root declarative base for all ORM models in the platform."""


class IdentityMixin:
    """Provides a UUID primary key column."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds created_at and updated_at columns with automatic server-side defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds soft-delete support via is_active flag and deleted_at timestamp.

    Soft-deleted records have is_active=False and a non-null deleted_at.
    Hard deletion occurs only after a configurable retention period.
    """

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


class TenantMixin:
    """Enforces tenant isolation at the model level.

    Every tenant-scoped table MUST include this mixin. The tenant_id column
    is indexed and must be included in every query filter.

    Enforcement at the query level is handled by middleware in the API layer,
    not by this mixin — this mixin only defines the column.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Domain Enumerations
# ---------------------------------------------------------------------------


class PlanTier(StrEnum):
    """Organization subscription tier."""

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class MailAccountStatus(StrEnum):
    """Mail account connection status."""

    ACTIVE = "active"
    SYNCING = "syncing"
    ERROR = "error"
    DISCONNECTED = "disconnected"
    SUSPENDED = "suspended"


class MailAccountType(StrEnum):
    """Type of mail account."""

    USER = "user"
    SHARED = "shared"
    SERVICE = "service"


class IdentityProvider(StrEnum):
    """Supported SSO identity providers."""

    MICROSOFT = "microsoft"
    GOOGLE = "google"
    OKTA = "okta"
    SAML = "saml"


class ProviderType(StrEnum):
    """Supported mail provider types."""

    MICROSOFT_GRAPH = "microsoft_graph"
    GMAIL = "gmail"
    IMAP = "imap"


class SystemRole(StrEnum):
    """System-defined role names (immutable)."""

    PLATFORM_ADMIN = "platform_admin"
    ORG_OWNER = "org_owner"
    TENANT_ADMIN = "tenant_admin"
    MEMBER = "member"
    VIEWER = "viewer"


class DataRegion(StrEnum):
    """Supported data residency regions."""

    US_EAST = "us-east"
    US_WEST = "us-west"
    EU_WEST = "eu-west"
    AP_SOUTHEAST = "ap-southeast"
