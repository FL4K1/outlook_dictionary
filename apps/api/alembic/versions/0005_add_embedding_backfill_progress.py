"""Add embedding backfill progress table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-20

Adds the durable checkpoint table for resumable, tenant-scoped
embedding backfill (PR-2.9).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_backfill_progress",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("last_cursor_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "total_processed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_embedded",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_skipped",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_failed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("embedding_dims", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "embedding_model",
            name="uq_embedding_backfill_tenant_model",
        ),
    )
    op.create_index(
        op.f("ix_embedding_backfill_progress_tenant_id"),
        "embedding_backfill_progress",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_embedding_backfill_progress_tenant_id"),
        table_name="embedding_backfill_progress",
    )
    op.drop_table("embedding_backfill_progress")
