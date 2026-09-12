"""Repository layer for database access."""

from app.repositories.auth import (
    DeviceSessionRepository,
    RefreshTokenFamilyRepository,
    SessionRepository,
)
from app.repositories.base import BaseRepository
from app.repositories.core import (
    OrganizationRepository,
    RoleRepository,
    TenantRepository,
    UserRepository,
)
from app.repositories.mail import (
    MailAccountRepository,
    MailFolderRepository,
    MailMessageFolderRepository,
    MailMessageParticipantRepository,
    MailMessageRepository,
    MailSyncStateRepository,
    OutboxEventRepository,
)

__all__ = [
    "BaseRepository",
    "DeviceSessionRepository",
    "MailAccountRepository",
    "MailFolderRepository",
    "MailMessageFolderRepository",
    "MailMessageParticipantRepository",
    "MailMessageRepository",
    "MailSyncStateRepository",
    "OrganizationRepository",
    "OutboxEventRepository",
    "RefreshTokenFamilyRepository",
    "RoleRepository",
    "SessionRepository",
    "TenantRepository",
    "UserRepository",
]
