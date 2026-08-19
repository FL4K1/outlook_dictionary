"""Identity provider credential and OAuth state models.

Separate from mail-scoped ProviderCredential. These models support
external identity provider authentication (Microsoft Entra ID).
"""

from __future__ import annotations

import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mip_models.base import (
    Base,
    IdentityMixin,
    TimestampMixin,
)


class IdentityProviderCredential(Base, IdentityMixin, TimestampMixin):
    """Encrypted OAuth tokens for an external identity provider.

    Separate from mail-scoped ProviderCredential. Stores identity provider
    access/refresh tokens encrypted at rest.
    """

    __tablename__ = "identity_provider_credentials"

    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            name="uq_identity_provider_credential_identity",
        ),
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        comment="FK to identities.id, one credential set per identity",
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Denormalized for query safety and tenant isolation",
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Identity provider type, e.g., microsoft",
    )

    encrypted_access_token: Mapped[bytes] = mapped_column(
        nullable=False,
        comment="AES-256-GCM encrypted access token",
    )

    encrypted_refresh_token: Mapped[bytes] = mapped_column(
        nullable=False,
        comment="AES-256-GCM encrypted refresh token",
    )

    token_expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
        index=True,
        comment="When the access token expires",
    )

    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String),
        nullable=True,
        comment="OAuth scopes granted",
    )

    encryption_key_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Identifier of the DEK used for encryption",
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="NULL = active; non-NULL = revoked",
    )


class EntraTenantMapping(Base, IdentityMixin, TimestampMixin):
    """Maps an Entra tenant ID to a platform Tenant.

    This is the authoritative tenant resolution mechanism for PR-1.3.
    Entra tenant ID must map to exactly one platform Tenant.
    """

    __tablename__ = "entra_tenant_mappings"

    __table_args__ = (
        UniqueConstraint(
            "entra_tenant_id",
            name="uq_entra_tenant_mapping_tenant",
        ),
    )

    entra_tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Entra tenant ID (GUID or tenant domain)",
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Platform Tenant ID",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        comment="Whether this mapping is active",
    )


class OAuthState(Base, IdentityMixin, TimestampMixin):
    """OAuth state/nonce/PKCE verifier for CSRF and replay protection.

    Server-side database-backed state store per AD-PR13-006.
    """

    __tablename__ = "oauth_states"

    __table_args__ = (UniqueConstraint("state", name="uq_oauth_state_state"),)

    state: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Cryptographic state token for CSRF protection",
    )

    nonce: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Cryptographic nonce for ID token replay protection",
    )

    code_verifier: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="PKCE code verifier (never exposed to client)",
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Identity provider type, e.g., microsoft",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="State expiration timestamp",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="NULL = unconsumed; non-NULL = consumed timestamp",
    )

    request_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Correlation request ID for audit trail",
    )
