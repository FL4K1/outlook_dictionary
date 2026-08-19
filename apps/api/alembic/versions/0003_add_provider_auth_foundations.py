"""Add provider authentication foundation tables and DeviceSession identity link.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

Adds the persistence foundation for PR-1.3 Provider Integration:
- identity_provider_credentials: encrypted Entra ID access/refresh tokens
- oauth_states: server-side OAuth state/nonce/PKCE storage
- entra_tenant_mappings: Entra tenant ID to platform Tenant mapping
- device_sessions.identity_id: FK to identities for provider derivation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add identity_id to device_sessions
    op.add_column(
        "device_sessions",
        sa.Column(
            "identity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="FK to identities.id for provider derivation",
        ),
    )

    # 2. Create identity_provider_credentials
    op.create_table(
        "identity_provider_credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "identity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("encrypted_access_token", sa.LargeBinary, nullable=False),
        sa.Column("encrypted_refresh_token", sa.LargeBinary, nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scopes", ARRAY(sa.String), nullable=True),
        sa.Column("encryption_key_id", sa.String(255), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "identity_id",
            name="uq_identity_provider_credential_identity",
        ),
    )
    op.create_index(
        op.f("ix_identity_provider_credentials_identity_id"),
        "identity_provider_credentials",
        ["identity_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_identity_provider_credentials_tenant_id"),
        "identity_provider_credentials",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_identity_provider_credentials_token_expires_at"),
        "identity_provider_credentials",
        ["token_expires_at"],
        unique=False,
    )

    # 3. Create oauth_states
    op.create_table(
        "oauth_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("state", sa.String(255), nullable=False),
        sa.Column("nonce", sa.String(255), nullable=False),
        sa.Column("code_verifier", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("state", name="uq_oauth_state_state"),
    )
    op.create_index(
        op.f("ix_oauth_states_state"),
        "oauth_states",
        ["state"],
        unique=True,
    )
    op.create_index(
        op.f("ix_oauth_states_expires_at"),
        "oauth_states",
        ["expires_at"],
        unique=False,
    )

    # 4. Create entra_tenant_mappings
    op.create_table(
        "entra_tenant_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entra_tenant_id",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "entra_tenant_id",
            name="uq_entra_tenant_mapping_tenant",
        ),
    )
    op.create_index(
        op.f("ix_entra_tenant_mappings_entra_tenant_id"),
        "entra_tenant_mappings",
        ["entra_tenant_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_entra_tenant_mappings_tenant_id"),
        "entra_tenant_mappings",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Drop entra_tenant_mappings
    op.drop_index(op.f("ix_entra_tenant_mappings_tenant_id"), table_name="entra_tenant_mappings")
    op.drop_index(
        op.f("ix_entra_tenant_mappings_entra_tenant_id"),
        table_name="entra_tenant_mappings",
    )
    op.drop_table("entra_tenant_mappings")

    # 2. Drop oauth_states
    op.drop_index(op.f("ix_oauth_states_expires_at"), table_name="oauth_states")
    op.drop_index(op.f("ix_oauth_states_state"), table_name="oauth_states")
    op.drop_table("oauth_states")

    # 3. Drop identity_provider_credentials
    op.drop_index(
        op.f("ix_identity_provider_credentials_token_expires_at"),
        table_name="identity_provider_credentials",
    )
    op.drop_index(
        op.f("ix_identity_provider_credentials_tenant_id"),
        table_name="identity_provider_credentials",
    )
    op.drop_index(
        op.f("ix_identity_provider_credentials_identity_id"),
        table_name="identity_provider_credentials",
    )
    op.drop_table("identity_provider_credentials")

    # 4. Remove identity_id from device_sessions
    op.drop_column("device_sessions", "identity_id")
