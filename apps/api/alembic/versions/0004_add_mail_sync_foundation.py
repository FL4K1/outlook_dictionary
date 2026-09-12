"""Add mail synchronization database foundation tables and mail_accounts fields.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11

Adds the persistence foundation for PR-2.1 Mail Synchronization:
- mail_accounts additions (credential_generation, refresh lease fields)
- mail_folders
- mail_sync_states
- mail_messages
- mail_message_folders
- mail_message_participants
- outbox_events
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. MailAccount additions & composite constraint
    op.add_column(
        "mail_accounts",
        sa.Column(
            "credential_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "mail_accounts",
        sa.Column(
            "refresh_locked_by",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "mail_accounts",
        sa.Column(
            "refresh_locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "mail_accounts",
        sa.Column(
            "refresh_lease_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_unique_constraint(
        "uq_mail_accounts_id_tenant_id",
        "mail_accounts",
        ["id", "tenant_id"],
    )

    # 2. MailFolders
    op.create_table(
        "mail_folders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mail_account_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_folder_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "mail_account_id",
            name="uq_mail_folders_id_tenant_account",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_mail_folders_id_tenant_id",
        ),
        sa.UniqueConstraint(
            "mail_account_id",
            "provider_folder_id",
            name="uq_mail_folder_prov",
        ),
        sa.ForeignKeyConstraint(
            ["mail_account_id", "tenant_id"],
            ["mail_accounts.id", "mail_accounts.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "tenant_id", "mail_account_id"],
            ["mail_folders.id", "mail_folders.tenant_id", "mail_folders.mail_account_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        op.f("ix_mail_folders_tenant_id"),
        "mail_folders",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mail_folders_mail_account_id"),
        "mail_folders",
        ["mail_account_id"],
        unique=False,
    )

    # 3. MailSyncStates
    op.create_table(
        "mail_sync_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mail_folder_id", UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(50), nullable=False),
        sa.Column("sync_token", sa.Text(), nullable=True),
        sa.Column(
            "resync_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("locked_by", UUID(as_uuid=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "lease_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("mail_folder_id", name="uq_mail_sync_states_folder_id"),
        sa.ForeignKeyConstraint(
            ["mail_folder_id", "tenant_id"],
            ["mail_folders.id", "mail_folders.tenant_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_mail_sync_states_tenant_id"),
        "mail_sync_states",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mail_sync_states_mail_folder_id"),
        "mail_sync_states",
        ["mail_folder_id"],
        unique=True,
    )

    # 4. MailMessages
    op.create_table(
        "mail_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mail_account_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_message_id", sa.String(512), nullable=False),
        sa.Column(
            "version",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", JSONB, nullable=True),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("sender", JSONB, nullable=True),
        sa.Column("received_date_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "has_attachments",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "mail_account_id",
            name="uq_mail_messages_id_tenant_account",
        ),
        sa.UniqueConstraint(
            "mail_account_id",
            "provider_message_id",
            name="uq_mail_message_prov",
        ),
        sa.ForeignKeyConstraint(
            ["mail_account_id", "tenant_id"],
            ["mail_accounts.id", "mail_accounts.tenant_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_mail_messages_tenant_id"),
        "mail_messages",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mail_messages_mail_account_id"),
        "mail_messages",
        ["mail_account_id"],
        unique=False,
    )

    # 5. MailMessageFolders (Pivot)
    op.create_table(
        "mail_message_folders",
        sa.Column("mail_message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mail_folder_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mail_account_id", UUID(as_uuid=True), nullable=False),
        sa.Column("resync_generation", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "mail_message_id",
            "mail_folder_id",
            name="pk_mail_message_folders",
        ),
        sa.ForeignKeyConstraint(
            ["mail_message_id", "tenant_id", "mail_account_id"],
            ["mail_messages.id", "mail_messages.tenant_id", "mail_messages.mail_account_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mail_folder_id", "tenant_id", "mail_account_id"],
            ["mail_folders.id", "mail_folders.tenant_id", "mail_folders.mail_account_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_mail_message_folders_resync",
        "mail_message_folders",
        ["mail_folder_id", "resync_generation"],
        unique=False,
    )

    # 6. MailMessageParticipants
    op.create_table(
        "mail_message_participants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mail_message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.CheckConstraint(
            "role IN ('TO', 'CC', 'BCC')",
            name="ck_mail_message_participants_role",
        ),
        sa.ForeignKeyConstraint(
            ["mail_message_id"],
            ["mail_messages.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_mail_message_participants_msg",
        "mail_message_participants",
        ["mail_message_id"],
        unique=False,
    )

    # 7. OutboxEvents
    op.create_table(
        "outbox_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("locked_by", UUID(as_uuid=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "lease_version",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_events_idempotency_key"),
    )
    op.create_index(
        op.f("ix_outbox_events_tenant_id"),
        "outbox_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "idx_outbox_events_pending",
        "outbox_events",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING', 'IN_FLIGHT')"),
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_events_pending", table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_tenant_id"), table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index("idx_mail_message_participants_msg", table_name="mail_message_participants")
    op.drop_table("mail_message_participants")

    op.drop_index("idx_mail_message_folders_resync", table_name="mail_message_folders")
    op.drop_table("mail_message_folders")

    op.drop_index(op.f("ix_mail_messages_mail_account_id"), table_name="mail_messages")
    op.drop_index(op.f("ix_mail_messages_tenant_id"), table_name="mail_messages")
    op.drop_table("mail_messages")

    op.drop_index(op.f("ix_mail_sync_states_mail_folder_id"), table_name="mail_sync_states")
    op.drop_index(op.f("ix_mail_sync_states_tenant_id"), table_name="mail_sync_states")
    op.drop_table("mail_sync_states")

    op.drop_index(op.f("ix_mail_folders_mail_account_id"), table_name="mail_folders")
    op.drop_index(op.f("ix_mail_folders_tenant_id"), table_name="mail_folders")
    op.drop_table("mail_folders")

    op.drop_constraint("uq_mail_accounts_id_tenant_id", "mail_accounts", type_="unique")
    op.drop_column("mail_accounts", "refresh_lease_version")
    op.drop_column("mail_accounts", "refresh_locked_until")
    op.drop_column("mail_accounts", "refresh_locked_by")
    op.drop_column("mail_accounts", "credential_generation")
