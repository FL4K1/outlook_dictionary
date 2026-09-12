"""Unit tests for mail synchronization models."""

from __future__ import annotations

from sqlalchemy import inspect

from mip_models.mail import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailMessageFolder,
    MailMessageParticipant,
    MailSyncState,
    OutboxEvent,
)


class TestMailAccountModel:
    """Tests for MailAccount additions."""

    def test_table_name(self) -> None:
        assert MailAccount.__tablename__ == "mail_accounts"

    def test_sync_foundation_columns(self) -> None:
        mapper = inspect(MailAccount)
        cred_gen = mapper.columns["credential_generation"]
        ref_locked_by = mapper.columns["refresh_locked_by"]
        ref_locked_until = mapper.columns["refresh_locked_until"]
        ref_lease_ver = mapper.columns["refresh_lease_version"]

        assert cred_gen.nullable is False
        assert ref_locked_by.nullable is True
        assert ref_locked_until.nullable is True
        assert ref_lease_ver.nullable is False

    def test_id_tenant_id_unique_constraint(self) -> None:
        assert any(
            getattr(c, "name", None) == "uq_mail_accounts_id_tenant_id"
            for c in MailAccount.__table_args__
        )


class TestMailFolderModel:
    """Tests for MailFolder model definition."""

    def test_table_name(self) -> None:
        assert MailFolder.__tablename__ == "mail_folders"

    def test_columns(self) -> None:
        mapper = inspect(MailFolder)
        assert mapper.columns["tenant_id"].nullable is False
        assert mapper.columns["mail_account_id"].nullable is False
        assert mapper.columns["provider_folder_id"].nullable is False
        assert mapper.columns["name"].nullable is False
        assert mapper.columns["parent_id"].nullable is True
        assert mapper.columns["is_active"].nullable is False

    def test_unique_constraints(self) -> None:
        names = {getattr(c, "name", None) for c in MailFolder.__table_args__}
        assert "uq_mail_folders_id_tenant_account" in names
        assert "uq_mail_folder_prov" in names


class TestMailSyncStateModel:
    """Tests for MailSyncState model definition."""

    def test_table_name(self) -> None:
        assert MailSyncState.__tablename__ == "mail_sync_states"

    def test_columns(self) -> None:
        mapper = inspect(MailSyncState)
        assert mapper.columns["tenant_id"].nullable is False
        assert mapper.columns["mail_folder_id"].nullable is False
        assert mapper.columns["state"].nullable is False
        assert mapper.columns["sync_token"].nullable is True
        assert mapper.columns["resync_generation"].nullable is False
        assert mapper.columns["lease_version"].nullable is False

    def test_unique_folder_constraint(self) -> None:
        assert any(
            getattr(c, "name", None) == "uq_mail_sync_states_folder_id"
            for c in MailSyncState.__table_args__
        )


class TestMailMessageModel:
    """Tests for MailMessage model definition."""

    def test_table_name(self) -> None:
        assert MailMessage.__tablename__ == "mail_messages"

    def test_columns(self) -> None:
        mapper = inspect(MailMessage)
        assert mapper.columns["tenant_id"].nullable is False
        assert mapper.columns["mail_account_id"].nullable is False
        assert mapper.columns["provider_message_id"].nullable is False
        assert mapper.columns["version"].nullable is False
        assert mapper.columns["is_deleted"].nullable is False

    def test_unique_constraints(self) -> None:
        names = {getattr(c, "name", None) for c in MailMessage.__table_args__}
        assert "uq_mail_messages_id_tenant_account" in names
        assert "uq_mail_message_prov" in names


class TestMailMessageFolderModel:
    """Tests for MailMessageFolder pivot model definition."""

    def test_table_name(self) -> None:
        assert MailMessageFolder.__tablename__ == "mail_message_folders"

    def test_columns(self) -> None:
        mapper = inspect(MailMessageFolder)
        assert mapper.columns["mail_message_id"].nullable is False
        assert mapper.columns["mail_folder_id"].nullable is False
        assert mapper.columns["tenant_id"].nullable is False
        assert mapper.columns["mail_account_id"].nullable is False
        assert mapper.columns["resync_generation"].nullable is False


class TestMailMessageParticipantModel:
    """Tests for MailMessageParticipant model definition."""

    def test_table_name(self) -> None:
        assert MailMessageParticipant.__tablename__ == "mail_message_participants"

    def test_columns(self) -> None:
        mapper = inspect(MailMessageParticipant)
        assert mapper.columns["mail_message_id"].nullable is False
        assert mapper.columns["email"].nullable is False
        assert mapper.columns["role"].nullable is False

    def test_role_check_constraint(self) -> None:
        assert any(
            getattr(c, "name", None) == "ck_mail_message_participants_role"
            for c in MailMessageParticipant.__table_args__
        )


class TestOutboxEventModel:
    """Tests for OutboxEvent model definition."""

    def test_table_name(self) -> None:
        assert OutboxEvent.__tablename__ == "outbox_events"

    def test_columns(self) -> None:
        mapper = inspect(OutboxEvent)
        assert mapper.columns["tenant_id"].nullable is False
        assert mapper.columns["aggregate_id"].nullable is False
        assert mapper.columns["aggregate_version"].nullable is False
        assert mapper.columns["event_type"].nullable is False
        assert mapper.columns["idempotency_key"].nullable is False
        assert mapper.columns["payload"].nullable is False
        assert mapper.columns["status"].nullable is False
        assert mapper.columns["lease_version"].nullable is False

    def test_idempotency_key_unique(self) -> None:
        assert any(
            getattr(c, "name", None) == "uq_outbox_events_idempotency_key"
            for c in OutboxEvent.__table_args__
        )
