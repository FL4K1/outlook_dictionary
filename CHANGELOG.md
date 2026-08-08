# Changelog

All notable changes to this project will be documented in this file.

## v0.3.0-alpha.1 - Authentication Foundation & Token Infrastructure

*This release officially closes the Authentication Foundation milestone, combining the scope of PR-1.2.1 and PR-1.2.2 into a single alpha release.*

### Added

- Provider-agnostic authentication foundation module.
- Immutable `AuthenticationContext` for request-scoped identity/session context.
- Centralized `SecurityEvent` abstraction and structured event emitter.
- Authentication-specific exception hierarchy.
- `TokenService` with minimal JWT access-token claims and opaque refresh-token generation.
- `SigningProvider` interface with initial HS256 HMAC implementation.
- `SessionService` for session creation, refresh rotation, timeout checks, revocation, and refresh-token reuse detection.
- `SessionRepository` for session lookup and revocation operations.
- JWT algorithm allow-list enforcement for the token signing path.
- Production-safe JWT signing-secret validation.
- Minimum HS256 signing-secret length validation.
- Project-specific required JWT claim validation for `sid`, `tid`, and `oid`.
- Forbidden JWT authorization-claim validation for roles, permissions, mailbox IDs, and provider tokens.
- Runtime-checkable `SigningProvider` protocol for substitution tests and future signing providers.
- Focused unit tests for auth context, security events, token handling, session lifecycle, token security, and authentication service orchestration.
- Authentication configuration in `Settings` and `.env.example`.

### Changed

- PR-1.2 scope was explicitly split into smaller implementation slices, combining PR-1.2.1 and PR-1.2.2.
- JWT claims are constrained to stable identity/session fields only; roles and permissions remain server-resolved.
- Token verification now validates both library-level JWT claims and project-specific session/tenant/organization claims.
- Token configuration now fails closed for unsupported algorithms and unsafe HS256 secrets.

### Deferred

- Authentication middleware and `AuthenticationContext` request injection.
- Authorization middleware and `PolicyEngine`.
- Authentication API endpoints.
- Capability discovery endpoint.
- External Identity Provider integrations.
- Microsoft Entra OAuth, identity linking, and user provisioning.
- Durable audit-log persistence and SIEM/export sinks.
- Full key-management provider and signing-key rotation.
- Asymmetric signing and JWKS support.

### Known Limitations

- Session identity is now decoupled from refresh-token rotation in PR-1.2.3. Legacy `Session` rows are retained during transition.
- HS256 remains the only supported algorithm in this alpha slice.
- Mypy validation is blocked locally by Windows Application Control and must pass in a trusted environment before final merge approval.


