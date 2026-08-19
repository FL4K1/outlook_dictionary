# Current Sprint

> This file describes ONLY the active engineering sprint. Update at each sprint transition.

---

## Sprint: Authentication APIs (PR-1.2.5)
**Status**: Merged — Released as v0.3.0-alpha.4.

---

## Objectives
Implement user-facing authentication API endpoints (`/auth/*`) for token refresh, token issuance, and session revocation.

## Scope
- `POST /auth/token` — OAuth2-compatible token endpoint (grant_type=refresh_token only).
- `POST /auth/refresh` — Exchange valid refresh token for new access token + refresh token pair.
- `POST /auth/logout` — Revoke a single refresh token.
- `POST /auth/logout-all` — Revoke all refresh tokens for a session.
- Public route registration for all four endpoints.
- Pydantic request/response schemas.
- Security event emission for all authentication outcomes.
- Request ID propagation into security events.

## Out of Scope
- Microsoft Entra ID OAuth flow (PR-1.3).
- MFA / TOTP.
- Frontend authentication UI.
- Service-to-service authentication.
- Rate limiting, brute-force protection, durable audit storage, Redis caching.
- Mail sync/search/AI security.

## Dependencies
- **Depends on:** Authentication Foundation (v0.3.0-alpha.1), Session Infrastructure (PR-1.2.3), Middleware & Authorization (PR-1.2.4).
- **Required by:** Provider Integration (PR-1.3).

## Completed Work
- [x] `POST /auth/refresh` — valid/invalid/consumed/revoked/expired refresh token handling.
- [x] `POST /auth/token` — OAuth2 token endpoint with grant_type=refresh_token validation.
- [x] `POST /auth/logout` — single session revocation with idempotent 204.
- [x] `POST /auth/logout-all` — user-global session revocation with per-session events.
- [x] Public route registration — all four endpoints registered as public routes.
- [x] Request/response schemas — Pydantic models with validation.
- [x] Security events — TOKEN_REFRESHED, TOKEN_REFRESH_FAILED, SESSION_REUSE_DETECTED, ALL_SESSIONS_REVOKED, SESSION_EXPIRED, SESSION_REVOKED, TOKEN_INVALID.
- [x] Request ID propagation — all security events include request_id from RequestIdMiddleware.
- [x] Tenant resolution via session.tenant_id.
- [x] Organization ID resolution from Tenant.
- [x] Access token subject integrity — server-derived only, no client-supplied claims.
- [x] Stable DeviceSession identity across refresh.
- [x] expires_in calculation from settings.
- [x] No raw token leakage in events or errors.
- [x] Unit tests — 69 auth/security tests passing.
- [x] Security tests — token leakage, request_id propagation, event metadata, OAuth2 error contract, WWW-Authenticate header.
- [x] Integration test scaffolding — deferred (requires PostgreSQL).
- [x] Phase 5 security event verification complete.
- [x] F-001 reconciliation — grant_type=str vs Literal["refresh_token"] documented in EDD.

## Current Risks
- SR-053–SR-060 verification blocked — authoritative EDD source text for these requirements is unavailable.
- Database-dependent integration validation deferred — local PostgreSQL test environment unavailable.
- Test coverage gaps — 11 security-critical paths lack dedicated integration tests (mocked control flow verified).

## Current Blockers
- **SR-053–SR-060 Source Text**: The approved PR-1.2.4 EDD is present and covers SR-025 through SR-052. Requirements SR-053 through SR-060 cannot be verified because their authoritative source text is unavailable. This does not block the verified portion of the implementation but prevents full EDD compliance claims.
- **MyPy**: Local execution blocked by Windows Application Control.
- **PostgreSQL Integration Tests**: Deferred per project convention (requires live DB environment).

---

## Post-Merge State
PR-1.2.5 has been merged into main and released as v0.3.0-alpha.4.

## Next Sprint
**Sprint: Provider Integration (PR-1.3)**
**Status**: Blocked — Awaiting approved EDD.

### Objectives
Implement Microsoft Entra ID OAuth 2.0 / OIDC provider integration with identity linking and provider credential storage.

### Prerequisites
- [ ] **PR-1.3 EDD**: The approved Engineering Design Document for Provider Integration must be located and verified before implementation begins.
- [ ] **Updated main**: PR-1.2.5 must be merged and released (complete).

### Scope
- Microsoft Entra ID OAuth 2.0 / OIDC flow.
- Identity linking between platform accounts and provider identities.
- Provider credential storage and refresh.
- Security event emission for provider authentication outcomes.

### Out of Scope
- MFA / TOTP.
- Frontend authentication UI.
- Service-to-service authentication.
- Rate limiting and brute-force protection.
- Mail sync/search/AI security.

## Required Documents Before Implementation
- [ ] **PR-1.3 EDD**: Engineering Design Document for Provider Integration.
- [ ] **Threat Model**: PR-1.3 Security Threat Model (approved).

## Deliverables
- [x] `AuthenticationMiddleware` — JWT verification, session validation, context injection.
- [x] `AuthenticationContext` — immutable, server-generated request-scoped object.
- [x] `PolicyEngine` — default-deny authorization with `authorize()` and `AuthorizationDecision`.
- [x] Authorization dependencies — `require_permission()`, `require_role()`, `require_tenant_membership()`.
- [x] `PublicRoutes` — explicit allow-list for unauthenticated endpoints.
- [x] `MembershipRepository` — active membership resolution.
- [x] Security events — authentication and authorization outcome emission.
- [x] Unit tests — 43 PR-1.2.4 tests passing.
- [x] Security tests — token leakage, jti presence, default-deny, malformed claims, middleware ordering, context isolation.
- [x] Authentication API endpoints — POST /auth/token, POST /auth/refresh, POST /auth/logout, POST /auth/logout-all.
- [x] Authentication API tests — 69 unit tests passing.
- [x] Security event verification — Phase 5 complete with request_id propagation.
- [ ] Integration tests — deferred (PostgreSQL-dependent).
- [x] Updated Technical Debt Register and CHANGELOG.

## Validation Gates
- [x] PR-1.2.4 unit tests pass (43/43).
- [x] PR-1.2.5 unit tests pass (69/69).
- [x] Full unit suite passes (131 passed; 3 pre-existing db_session fixture errors unrelated to PR-1.2.4/1.2.5).
- [x] `ruff check` passes.
- [x] `ruff format --check` passes.
- [ ] `mypy --strict` — 2 pre-existing `app_log_format` errors in test helpers (unrelated to PR-1.2.4/1.2.5).
- [ ] `alembic` migration verification — deferred (requires PostgreSQL).
- [ ] Integration tests — deferred (requires PostgreSQL testcontainers).

## Integration Test Status
PostgreSQL-backed integration tests (DeviceSession revocation, tenant mismatch, membership resolution, role/permission loading) are **deferred** because a local PostgreSQL test environment is unavailable. These tests require:
- Live PostgreSQL instance
- Async concurrency test infrastructure
- Database migration verification

**Rationale:** Windows Application Control blocks local PostgreSQL execution. Integration tests will be completed when PostgreSQL becomes available in CI, or when PR-1.3 provider integration provides additional test infrastructure.

## EDD Compliance Status
- **PR-1.2.4 (SR-025 through SR-052)**: Implemented and verified against the approved EDD.
- **PR-1.2.5**: Implemented and verified against the approved EDD (`docs/reviews/PR-1.2.5-authentication-apis-edd.md`).
- **SR-053–SR-060**: Cannot be verified. Their authoritative source text is unavailable in the current EDD. No compliance claim is made for these requirements.

## Definition of Done
- [x] Authentication enforcement gap closed.
- [x] Fail-closed middleware behavior implemented and tested.
- [x] JWT sid/tid/oid claim enforcement implemented.
- [x] DeviceSession validation (revoked, expired, idle) implemented.
- [x] Tenant/organization isolation implemented.
- [x] Membership validation implemented.
- [x] PolicyEngine follows approved EDD API (`authorize()`, `AuthorizationDecision`).
- [x] Authorization dependencies follow approved EDD Section 8.
- [x] Authorization security events emitted for all allow/deny decisions.
- [x] Resource ownership verification implemented.
- [x] Public route allow-list implemented.
- [x] Authentication APIs implemented and released as v0.3.0-alpha.4.
- [x] Security events verified with request_id propagation.
- [x] All deliverables checked in.
- [x] Unit validation gates passed.
- [ ] Integration tests deferred (PostgreSQL-dependent tests require live DB environment).
- [ ] Full SR-025–SR-060 verification pending availability of complete EDD source text for SR-053–SR-060.
