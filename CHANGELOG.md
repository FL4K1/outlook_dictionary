# Changelog

All notable changes to this project will be documented in this file.

## v0.3.0-alpha.3 - Middleware & Authorization (PR-1.2.4)

*This release introduces request-time authentication and authorization enforcement, closing the gap between token infrastructure and API protection.*

### Added

- `AuthenticationMiddleware` — outermost FastAPI middleware that verifies JWT access tokens, validates backing `DeviceSession` state, and hydrates an immutable `AuthenticationContext` per request.
- Fail-closed middleware behavior — when the session factory is unavailable, protected routes return `401 Unauthorized` and public routes are still allowed; no authentication bypass exists.
- JWT claim enforcement — `sid`, `tid`, and `oid` are now required claims in the JWT decoder, preventing tokens missing critical identity claims.
- `DeviceSession` validation — middleware checks revoked sessions, absolute timeout, and idle timeout, emitting security events for each failure path.
- Tenant and organization isolation — middleware verifies `DeviceSession.tenant_id == JWT.tid` and `Tenant.organization_id == JWT.oid`, rejecting cross-tenant and cross-organization token reuse.
- Membership validation — active membership is required for tenant access; missing or inactive membership results in `401 Unauthorized`.
- Immutable `AuthenticationContext` — frozen dataclass stored in `request.state`, containing server-resolved user identity, tenant/organization context, role IDs, role names, and permissions.
- `PolicyEngine` — centralized, default-deny authorization with `authorize(context, resource, action, resource_owner_id=None)` returning immutable `AuthorizationDecision` values.
- Resource ownership verification — `PolicyEngine.authorize()` supports `resource_owner_id` for ownership-based access grants.
- Authorization dependencies — FastAPI `Depends()` primitives: `require_permission()`, `require_role()`, and `require_tenant_membership()`.
- Public route allow-list — explicit frozenset of exempt routes (`/health/*`, `/auth/token`, `/auth/refresh`, `/docs`, `/redoc`, `/openapi.json`); all other routes default to protected.
- Authorization security events — `AUTHORIZATION_SUCCESS` and `AUTHORIZATION_FAILURE` events emitted for every allow/deny decision, with request ID propagation.
- Security tests — token leakage prevention, JTI presence-only validation, default-deny behavior, malformed claim rejection, middleware ordering, and `AuthenticationContext` isolation.
- `MembershipRepository` — database lookup for active user-tenant memberships.
- `get_session_factory()` accessor — application-scoped session factory for middleware and non-DI components.

### Changed

- `main.py` — `AuthenticationMiddleware` registered as outermost business middleware with `PolicyEngine` and `TokenService` dependencies.
- `AuthenticationContext` — extended with `role_names` field to support `require_role()` by name.
- Security events — added `TOKEN_VALIDATED`, `AUTHORIZATION_SUCCESS`, and `AUTHORIZATION_FAILURE` event types.

### Deferred

- Integration tests requiring PostgreSQL testcontainers (deferred per project convention; requires live DB environment).
- Full SR-025–SR-060 verification — SR-025 through SR-052 verified; SR-053 through SR-060 cannot be verified because their authoritative EDD source text is unavailable.
- Rate limiting, brute-force protection, durable audit storage, Redis authorization caching, service-to-service authentication.

### Known Limitations

- SR-053–SR-060 remain unverified because their source text is not present in the approved EDD.
- 11 security-critical paths lack dedicated integration tests (mocked control flow verified).
- PostgreSQL-backed integration tests deferred (requires live DB environment).
- Mypy validation blocked locally by Windows Application Control (2 pre-existing `app_log_format` errors in test helpers).
- `factory=None` error contract returns `AUTHENTICATION_SERVICE_UNAVAILABLE` rather than the EDD's generic `UNAUTHORIZED` code.

---

## v0.3.0-alpha.2 - Session Infrastructure (PR-1.2.3)

*This release decouples session identity from cryptographic token refresh epochs, introducing stable device sessions and refresh-token family tracking.*

### Added

- `DeviceSession` SQLAlchemy model — stable, user-visible authentication session tied to a specific device.
- `RefreshTokenFamily` model — tracks refresh-token epochs within a device session family.
- Session lifecycle management — creation, rotation, revocation, idle timeout, absolute timeout.
- Multi-device support — multiple active `DeviceSession` records per user.
- Device metadata storage — user agent and IP tracking at session creation.
- `DeviceSessionRepository` — persistence and lookup for device sessions.
- `RefreshTokenFamilyRepository` — epoch tracking, active-epoch lookup, and consumption marking.
- Atomic refresh with locking — `SELECT FOR UPDATE` primitives to prevent concurrent refresh race conditions.
- Refresh-token reuse detection — `SESSION_REUSE_DETECTED` security event emission.
- Session revocation — single-session and all-user-session revocation with `revoked_at` timestamps.
- Security events — `SESSION_CREATED`, `SESSION_REVOKED`, `SESSION_EXPIRED`, `SESSION_REUSE_DETECTED`, `ALL_SESSIONS_REVOKED`.
- Unit tests — 65/65 passing for session rotation, timeouts, revocation, and reuse detection.

### Changed

- Session identity is now decoupled from refresh-token rotation events.
- Legacy `Session` rows retained during transition period.

### Deferred

- Integration tests requiring PostgreSQL testcontainers (requires live DB environment).
- Request-time authentication middleware and PolicyEngine (deferred to PR-1.2.4).

### Known Limitations

- PostgreSQL-backed integration tests deferred (requires live DB environment).
- Mypy validation blocked locally by Windows Application Control.

---

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
