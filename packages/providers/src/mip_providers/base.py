"""Mail provider Protocol interfaces and canonical data models.

These interfaces define the contract that every mail provider implementation
must satisfy. The platform never interacts with provider-specific APIs
directly — all access goes through these abstractions.

Design decisions:
- Python Protocols (PEP 544) for structural subtyping — no forced inheritance.
- NormalizedEmail is frozen (immutable) to prevent mutation across async pipelines.
- provider_metadata dict exists as an escape hatch for provider-specific debugging data.
  Business logic MUST NEVER read from provider_metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class UnsetType:
    """Sentinel type representing an absent field in a provider update."""

    def __repr__(self) -> str:
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


UNSET = UnsetType()


@dataclass(frozen=True)
class ProviderEmailAddress:
    """A single provider email address with optional display name."""

    email: str
    name: str = ""


@dataclass(frozen=True)
class ProviderFolder:
    """Provider-neutral representation of a mail folder."""

    provider_folder_id: str
    name: str
    parent_id: str | None = None
    is_active: bool = True


@dataclass(frozen=True)
class ProviderRemoval:
    """Provider-neutral representation of a message removal from a folder scope."""

    provider_message_id: str
    reason: str = "folder_removed"


@dataclass(frozen=True)
class ProviderMessage:
    """Presence-aware provider message model supporting 3-way field semantics.

    - Present value: actual value (str, dict, list, datetime, bool, etc.)
    - Present null: None (explicitly cleared)
    - Absent field: UNSET (unmodified)
    """

    provider_message_id: str
    subject: str | UnsetType | None = UNSET
    body: dict[str, Any] | UnsetType | None = UNSET
    body_preview: str | UnsetType | None = UNSET
    sender: ProviderEmailAddress | UnsetType | None = UNSET
    recipients_to: list[ProviderEmailAddress] | UnsetType | None = UNSET
    recipients_cc: list[ProviderEmailAddress] | UnsetType | None = UNSET
    recipients_bcc: list[ProviderEmailAddress] | UnsetType | None = UNSET
    received_date_time: datetime | UnsetType | None = UNSET
    has_attachments: bool | UnsetType | None = UNSET
    is_read: bool | UnsetType | None = UNSET
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderDeltaPage:
    """Result page returned by a message delta fetch."""

    messages: list[ProviderMessage]
    removals: list[ProviderRemoval]
    next_continuation: str | None
    has_more: bool
    is_delta_checkpoint: bool


@dataclass(frozen=True)
class EmailAddress:
    """A single email address with optional display name."""

    email: str
    name: str = ""


@dataclass(frozen=True)
class TokenSet:
    """OAuth token pair returned by authentication providers."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedEmail:
    """Canonical email representation consumed by the entire platform.

    Every mail provider MUST map its native format to this structure.
    This is the single source of truth for what an "email" looks like
    inside the platform — no provider-specific types leak beyond the
    provider boundary.
    """

    provider_message_id: str
    internet_message_id: str | None
    in_reply_to: str | None
    references: list[str]
    subject: str
    sender: EmailAddress
    recipients_to: list[EmailAddress]
    recipients_cc: list[EmailAddress]
    recipients_bcc: list[EmailAddress]
    received_at: datetime
    sent_at: datetime | None
    importance: str  # "low" | "normal" | "high"
    is_read: bool
    categories: list[str]
    folder_id: str
    folder_name: str
    has_attachments: bool
    body_content_type: str  # "text" | "html"
    body_content: str
    raw_mime: bytes
    provider_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncResult:
    """Result of a sync operation from a provider."""

    new_emails: list[NormalizedEmail]
    updated_message_ids: list[str]
    deleted_message_ids: list[str]
    new_checkpoint: str
    has_more: bool


@runtime_checkable
class MailSyncProvider(Protocol):
    """Interface for synchronizing mail from a provider."""

    async def initial_sync(
        self,
        mailbox_id: str,
        folders: list[str] | None = None,
    ) -> AsyncIterator[SyncResult]:
        """Full initial sync. Yields pages of results."""
        ...

    async def incremental_sync(
        self,
        mailbox_id: str,
        checkpoint: str,
    ) -> SyncResult:
        """Delta sync from last checkpoint."""
        ...

    async def get_message_mime(self, message_id: str) -> bytes:
        """Download the full MIME content of a single message."""
        ...


@runtime_checkable
class MailAuthProvider(Protocol):
    """Interface for authenticating with a mail provider."""

    async def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Generate the OAuth authorization URL."""
        ...

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        """Exchange authorization code for tokens."""
        ...

    async def refresh_token(self, refresh_token: str) -> TokenSet:
        """Refresh an expired access token."""
        ...

    async def revoke_token(self, refresh_token: str) -> None:
        """Revoke a refresh token."""
        ...


@runtime_checkable
class MailWebhookProvider(Protocol):
    """Interface for managing provider-specific webhook subscriptions."""

    async def create_subscription(
        self,
        mailbox_id: str,
        notification_url: str,
    ) -> str:
        """Create a change notification subscription. Returns subscription ID."""
        ...

    async def renew_subscription(self, subscription_id: str) -> None:
        """Renew an expiring subscription."""
        ...

    async def delete_subscription(self, subscription_id: str) -> None:
        """Remove a subscription."""
        ...

    def validate_notification(self, payload: bytes, headers: dict[str, str]) -> bool:
        """Validate that a notification is authentic."""
        ...
