"""Mail API models and schemas."""

from __future__ import annotations

import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003

from pydantic import BaseModel


class MailAccountResponse(BaseModel):
    """Safe metadata representation of a connected mailbox."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email_address: str
    display_name: str | None = None
    status: str
    connected_at: datetime
    last_sync_at: datetime | None = None
    sync_error: str | None = None

    class Config:
        from_attributes = True


class MailFolderResponse(BaseModel):
    """Metadata representation of a synced mail folder."""

    id: uuid.UUID
    name: str
    provider_folder_id: str
    parent_id: uuid.UUID | None = None
    is_active: bool

    class Config:
        from_attributes = True


class PaginatedFoldersResponse(BaseModel):
    """Response containing paginated mail folders."""

    items: list[MailFolderResponse]
    total: int


class MailSyncStatusResponse(BaseModel):
    """Current synchronization state of a given folder."""

    mail_folder_id: uuid.UUID
    state: str
    last_sync: datetime
    resync_generation: int

    class Config:
        from_attributes = True


class MailSyncTriggerResponse(BaseModel):
    """Response indicating a sync job was scheduled."""

    mail_folder_id: uuid.UUID
    message: str


class DeactivateAccountResponse(BaseModel):
    """Response indicating a mail account was deactivated."""

    id: uuid.UUID
    status: str
