"""Provider Registry — runtime resolution of mail provider implementations.

The registry is populated at application startup. Sync workers and the mail
service resolve providers by type string (e.g., "microsoft_graph") without
knowing the concrete implementation class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mip_providers.base import MailAuthProvider, MailSyncProvider, MailWebhookProvider


class ProviderNotFoundError(Exception):
    """Raised when a requested provider type is not registered."""

    def __init__(self, provider_type: str) -> None:
        super().__init__(f"No provider registered for type: '{provider_type}'")
        self.provider_type = provider_type


class ProviderRegistry:
    """Registry for mail provider implementations.

    Usage::

        registry = ProviderRegistry()
        registry.register_sync("microsoft_graph", MicrosoftGraphSyncProvider)
        registry.register_auth("microsoft_graph", MicrosoftGraphAuthProvider)

        # Later, at runtime:
        sync_provider = registry.get_sync_provider("microsoft_graph", credentials=...)
    """

    def __init__(self) -> None:
        self._sync_providers: dict[str, type[Any]] = {}
        self._auth_providers: dict[str, type[Any]] = {}
        self._webhook_providers: dict[str, type[Any]] = {}

    def register_sync(
        self,
        provider_type: str,
        provider_class: type[Any],
    ) -> None:
        """Register a MailSyncProvider implementation for a provider type."""
        self._sync_providers[provider_type] = provider_class

    def register_auth(
        self,
        provider_type: str,
        provider_class: type[Any],
    ) -> None:
        """Register a MailAuthProvider implementation for a provider type."""
        self._auth_providers[provider_type] = provider_class

    def register_webhook(
        self,
        provider_type: str,
        provider_class: type[Any],
    ) -> None:
        """Register a MailWebhookProvider implementation for a provider type."""
        self._webhook_providers[provider_type] = provider_class

    def get_sync_provider(
        self,
        provider_type: str,
        **kwargs: Any,
    ) -> MailSyncProvider:
        """Instantiate and return a sync provider for the given type."""
        cls = self._sync_providers.get(provider_type)
        if cls is None:
            raise ProviderNotFoundError(provider_type)
        return cls(**kwargs)  # type: ignore[return-value]

    def get_auth_provider(
        self,
        provider_type: str,
        **kwargs: Any,
    ) -> MailAuthProvider:
        """Instantiate and return an auth provider for the given type."""
        cls = self._auth_providers.get(provider_type)
        if cls is None:
            raise ProviderNotFoundError(provider_type)
        return cls(**kwargs)  # type: ignore[return-value]

    def get_webhook_provider(
        self,
        provider_type: str,
        **kwargs: Any,
    ) -> MailWebhookProvider:
        """Instantiate and return a webhook provider for the given type."""
        cls = self._webhook_providers.get(provider_type)
        if cls is None:
            raise ProviderNotFoundError(provider_type)
        return cls(**kwargs)  # type: ignore[return-value]

    @property
    def registered_types(self) -> list[str]:
        """Return all provider types that have at least one registered implementation."""
        all_types = (
            set(self._sync_providers)
            | set(self._auth_providers)
            | set(self._webhook_providers)
        )
        return sorted(all_types)
