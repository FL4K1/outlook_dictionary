"""Audit logging models.

Records all security and access events. Append-only.
"""

from __future__ import annotations

import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mip_models.base import Base, IdentityMixin


class AuditLog(Base, IdentityMixin):
    """An immutable record of a security or access event."""

    __tablename__ = "audit_logs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="No FK constraint so logs survive user deletion",
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="No FK constraint so logs survive resource deletion",
    )

    metadata_dict: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "metadata",
        JSONB,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action='{self.action}')>"
