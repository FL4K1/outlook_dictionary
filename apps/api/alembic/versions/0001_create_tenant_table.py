"""create tenant table

Revision ID: 0001
Revises: None
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "slug",
            sa.String(100),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "plan_tier",
            sa.String(50),
            nullable=False,
            server_default="free",
        ),
        sa.Column("settings", JSONB, nullable=True, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tenant_slug", "tenant", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenant_slug", table_name="tenant")
    op.drop_table("tenant")
