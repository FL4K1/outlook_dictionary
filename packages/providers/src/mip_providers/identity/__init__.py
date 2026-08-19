"""Identity provider abstractions and interfaces.

Key exports:
- IdentityProviderAuth: Protocol for external identity provider authentication
- IdentityVerificationResult: Result of IdP identity verification
- ProviderCredentialSet: OAuth tokens returned by the identity provider
"""

from mip_providers.identity.base import (
    IdentityProviderAuth,
    IdentityVerificationResult,
    ProviderCredentialSet,
)

__all__ = [
    "IdentityProviderAuth",
    "IdentityVerificationResult",
    "ProviderCredentialSet",
]
