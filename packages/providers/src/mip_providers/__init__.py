"""MIP Providers — Mail provider abstractions and interfaces.

This package defines the provider-agnostic contracts that decouple the
platform from any specific mail system (Microsoft Graph, Gmail, IMAP, etc.).

Key exports:
- NormalizedEmail: Canonical email representation consumed by all downstream systems
- MailSyncProvider, MailAuthProvider, MailWebhookProvider: Protocol interfaces
- ProviderRegistry: Runtime resolution of provider implementations
"""

from mip_providers.base import (
    UNSET,
    EmailAddress,
    MailAuthProvider,
    MailSyncProvider,
    MailWebhookProvider,
    NormalizedEmail,
    ProviderDeltaPage,
    ProviderEmailAddress,
    ProviderFolder,
    ProviderMessage,
    ProviderRemoval,
    SyncResult,
    TokenSet,
    UnsetType,
)
from mip_providers.errors import (
    AuthExpiredError,
    DeltaCursorExpiredError,
    ProviderError,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitedError,
)
from mip_providers.mail.graph import MicrosoftGraphMailAdapter
from mip_providers.registry import ProviderRegistry

__all__ = [
    "UNSET",
    "AuthExpiredError",
    "DeltaCursorExpiredError",
    "EmailAddress",
    "MailAuthProvider",
    "MailSyncProvider",
    "MailWebhookProvider",
    "MicrosoftGraphMailAdapter",
    "NormalizedEmail",
    "ProviderDeltaPage",
    "ProviderEmailAddress",
    "ProviderError",
    "ProviderFolder",
    "ProviderMessage",
    "ProviderNotFoundError",
    "ProviderPermissionError",
    "ProviderRateLimitedError",
    "ProviderRegistry",
    "ProviderRemoval",
    "SyncResult",
    "TokenSet",
    "UnsetType",
]
