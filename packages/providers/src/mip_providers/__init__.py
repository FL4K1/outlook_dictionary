"""MIP Providers — Mail provider abstractions and interfaces.

This package defines the provider-agnostic contracts that decouple the
platform from any specific mail system (Microsoft Graph, Gmail, IMAP, etc.).

Key exports:
- NormalizedEmail: Canonical email representation consumed by all downstream systems
- MailSyncProvider, MailAuthProvider, MailWebhookProvider: Protocol interfaces
- ProviderRegistry: Runtime resolution of provider implementations
"""

from mip_providers.base import (
    EmailAddress,
    MailAuthProvider,
    MailSyncProvider,
    MailWebhookProvider,
    NormalizedEmail,
    SyncResult,
    TokenSet,
)
from mip_providers.registry import ProviderRegistry

__all__ = [
    "EmailAddress",
    "MailAuthProvider",
    "MailSyncProvider",
    "MailWebhookProvider",
    "NormalizedEmail",
    "ProviderRegistry",
    "SyncResult",
    "TokenSet",
]
