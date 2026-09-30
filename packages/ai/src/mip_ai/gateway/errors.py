"""Infrastructure-level exceptions for the shared LLM gateway."""


class GatewayError(Exception):
    """Base exception for all Gateway infrastructural errors."""


class GatewayConfigurationError(GatewayError):
    """Gateway is misconfigured (e.g., missing API keys, unsupported capability)."""


class GatewayTransientError(GatewayError):
    """Temporary Gateway failure (network, proxy timeout). Safe to retry conceptually."""


class GatewayRateLimitError(GatewayTransientError):
    """Gateway hit rate limit."""


class GatewayPermanentError(GatewayError):
    """Permanent provider failure (e.g., bad request parsing on provider side)."""
