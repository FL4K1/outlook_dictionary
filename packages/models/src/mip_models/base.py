"""SQLAlchemy base classes and reusable mixins.

Every ORM model in the platform inherits from Base.
Every tenant-scoped model additionally uses TenantMixin to enforce row-level isolation.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root declarative base for all ORM models in the platform."""


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


class IdentityMixin:
    """Provides a UUID primary key column."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class PlanTier(str):
    """Plan tier constants. Using plain strings instead of an enum
    to allow database-level flexibility without migrations on new tiers."""

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class AccountStatus(str):
    """Mail account connection status constants."""

    ACTIVE = "active"
    SYNCING = "syncing"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class SyncStatus(str):
    """Sync checkpoint status constants."""

    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
