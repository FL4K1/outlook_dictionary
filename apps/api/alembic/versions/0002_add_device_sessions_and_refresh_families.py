"""Add device_sessions and refresh_token_families tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07

Splits the monolithic Session model into:
- DeviceSession: stable user-visible session identity
- RefreshTokenFamily: individual refresh-token epochs within a session family

Existing Session rows are backfilled to preserve data in development environments.
The legacy sessions table is retained during the transition.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, UUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create device_sessions table
    op.create_table(
        "device_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "current_refresh_token_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 hash of the current refresh token",
        ),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip_address", INET, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
    )

    # 2. Create indexes for device_sessions
    op.create_index(
        op.f("ix_device_sessions_user_id"), "device_sessions", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_device_sessions_tenant_id"), "device_sessions", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_device_sessions_current_refresh_token_hash"),
        "device_sessions",
        ["current_refresh_token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_device_sessions_revoked_at"), "device_sessions", ["revoked_at"], unique=False
    )
    op.create_index(
        op.f("ix_device_sessions_expires_at"), "device_sessions", ["expires_at"], unique=False
    )

    # 3. Create refresh_token_families table
    op.create_table(
        "refresh_token_families",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("device_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 hash of the refresh token",
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When this refresh token was rotated/consumed",
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
    )

    # 4. Create indexes for refresh_token_families
    op.create_index(
        op.f("ix_refresh_token_families_device_session_id"),
        "refresh_token_families",
        ["device_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_token_families_token_hash"),
        "refresh_token_families",
        ["token_hash"],
        unique=True,
    )

    # 5. Backfill data from existing sessions table
    # Mapping: one legacy Session row -> one DeviceSession + one RefreshTokenFamily
    # Active sessions: consumed_at = NULL
    # Revoked sessions: consumed_at = revoked_at
    connection = op.get_bind()

    existing_sessions = connection.execute(
        sa.text(
            """
            SELECT id, user_id, tenant_id, refresh_token_hash,
                   user_agent, ip_address, expires_at, last_active_at, revoked_at,
                   created_at, updated_at
            FROM sessions
            """
        )
    )

    for row in existing_sessions.mappings():
        # Insert DeviceSession
        connection.execute(
            sa.text(
                """
                INSERT INTO device_sessions
                    (id, user_id, tenant_id, current_refresh_token_hash,
                     user_agent, ip_address, expires_at, last_active_at, revoked_at,
                     created_at, updated_at)
                VALUES
                    (:id, :user_id, :tenant_id, :current_refresh_token_hash,
                     :user_agent, :ip_address, :expires_at, :last_active_at, :revoked_at,
                     :created_at, :updated_at)
                """
            ),
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "tenant_id": row["tenant_id"],
                "current_refresh_token_hash": row["refresh_token_hash"],
                "user_agent": row["user_agent"],
                "ip_address": row["ip_address"],
                "expires_at": row["expires_at"],
                "last_active_at": row["last_active_at"],
                "revoked_at": row["revoked_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

        # Insert RefreshTokenFamily epoch
        consumed_at = row["revoked_at"]  # NULL for active sessions, non-NULL for revoked
        connection.execute(
            sa.text(
                """
                INSERT INTO refresh_token_families
                    (id, device_session_id, token_hash, consumed_at,
                     created_at, updated_at)
                VALUES
                    (:id, :device_session_id, :token_hash, :consumed_at,
                     :created_at, :updated_at)
                """
            ),
            {
                "id": row["id"],
                "device_session_id": row["id"],
                "token_hash": row["refresh_token_hash"],
                "consumed_at": consumed_at,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )


def downgrade() -> None:
    # 1. Drop indexes for refresh_token_families
    op.drop_index(op.f("ix_refresh_token_families_token_hash"), table_name="refresh_token_families")
    op.drop_index(
        op.f("ix_refresh_token_families_device_session_id"),
        table_name="refresh_token_families",
    )

    # 2. Drop refresh_token_families table
    op.drop_table("refresh_token_families")

    # 3. Drop indexes for device_sessions
    op.drop_index(op.f("ix_device_sessions_expires_at"), table_name="device_sessions")
    op.drop_index(op.f("ix_device_sessions_revoked_at"), table_name="device_sessions")
    op.drop_index(
        op.f("ix_device_sessions_current_refresh_token_hash"),
        table_name="device_sessions",
    )
    op.drop_index(op.f("ix_device_sessions_tenant_id"), table_name="device_sessions")
    op.drop_index(op.f("ix_device_sessions_user_id"), table_name="device_sessions")

    # 4. Drop device_sessions table
    # Legacy sessions table is preserved; data was backfilled, not moved.
    op.drop_table("device_sessions")
