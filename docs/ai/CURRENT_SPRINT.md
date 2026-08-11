# Current Sprint

> This file describes ONLY the active engineering sprint. Update at each sprint transition.

---

## Sprint: Middleware & Authorization (PR-1.2.4)
**Status**: Complete — Conditional Merge Readiness.

---

## Objectives
Close the authentication enforcement gap by introducing request-time JWT validation, DeviceSession verification, immutable AuthenticationContext hydration, and centralized authorization with default-deny semantics.

## Scope
- AuthenticationMiddleware — JWT verification and DeviceSession validation at HTTP boundary.
- AuthenticationContext — immutable, server-generated, request-scoped identity/authorization object.
- PolicyEngine — default-deny authorization decisions with resource/action semantics and ownership checks.
- Authorization dependencies — FastAPI Depends() primitives for route protection.
- Public route handling — explicit allow-list with default-deny semantics.
- Security event emission for authentication and authorization outcomes.

## Out of Scope
- OAuth implementation or Microsoft Entra ID integration.
- User-facing Authentication APIs (`/auth/*`).
- Rate limiting, brute-force protection, durable audit storage, Redis caching.
- Service-to-service authentication.
- Mail sync/search/AI security.

## Dependencies
- **Depends on:** Authentication Foundation (v0.3.0-alpha.1), Session Infrastructure (PR-1.2.3).
- **Required by:** Authentication APIs (PR-1.2.5).

## Completed Work
- [x] AuthenticationMiddleware with fail-closed factory=None behavior.
- [x] JWT claim enforcement — `sid`, `tid`, `oid` required in decoder.
- [x] DeviceSession validation — revoked, absolute timeout, idle timeout checks.
- [x] Tenant/organization isolation — DeviceSession.tenant_id == JWT.tid, Tenant.organization_id == JWT.oid.
- [x] Membership validation — active membership required for tenant access.
- [x] AuthenticationContext — frozen dataclass, server-generated per request, request-scoped.
- [x] PolicyEngine — `authorize(context, resource, action, resource_owner_id=None)` returning immutable `AuthorizationDecision`.
- [x] Authorization dependencies — `require_permission()`, `require_role()`, `require_tenant_membership()`.
- [x] Public route allow-list — explicit frozenset of exempt routes.
- [x] Security events — `TOKEN_VALIDATED`, `AUTHORIZATION_SUCCESS`, `AUTHORIZATION_FAILURE`, and all auth failure paths.
- [x] Request ID propagation into all security events.
- [x] Unit tests — 43 PR-1.2.4 tests passing.
- [x] Security tests — token leakage, jti presence-only, default-deny, malformed claims, middleware ordering, context isolation.
- [x] Integration test scaffolding — deferred (requires PostgreSQL).

## Current Risks
- SR-053–SR-060 verification blocked — authoritative EDD source text for these requirements is unavailable.
- Database-dependent integration validation deferred — local PostgreSQL test environment unavailable.
- Test coverage gaps — 11 security-critical paths lack dedicated integration tests (mocked control flow verified).

## Current Blockers
- **SR-053–SR-060 Source Text**: The approved PR-1.2.4 EDD is present and covers SR-025 through SR-052. Requirements SR-053 through SR-060 cannot be verified because their source text is unavailable. This does not block the verified portion of the implementation but prevents full EDD compliance claims.
- **MyPy**: Local execution blocked by Windows Application Control.
- **PostgreSQL Integration Tests**: Deferred per project convention (requires live DB environment).

---

## Required Documents Before Implementation
- [x] **Threat Model Requirements**: PR-1.2.4 Security Threat Model (approved).
- [x] **Required EDD**: Engineering Design Document (`docs/reviews/PR-1.2.4-middleware-authorization-edd.md`). Covers SR-025 through SR-052.
- [x] **Required Implementation Contract**: Approved boundaries and interfaces.

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
- [ ] Integration tests — deferred (PostgreSQL-dependent).
- [x] Updated Technical Debt Register and CHANGELOG.

## Validation Gates
- [x] PR-1.2.4 unit tests pass (43/43).
- [x] Full unit suite passes (131 passed; 3 pre-existing db_session fixture errors unrelated to PR-1.2.4).
- [x] `ruff check` passes.
- [x] `ruff format --check` passes.
- [ ] `mypy --strict` — 2 pre-existing `app_log_format` errors in test helpers (unrelated to PR-1.2.4).
- [ ] `alembic` migration verification — deferred (requires PostgreSQL).
- [ ] Integration tests — deferred (requires PostgreSQL testcontainers).

## Integration Test Status
PostgreSQL-backed integration tests (DeviceSession revocation, tenant mismatch, membership resolution, role/permission loading) are **deferred** because a local PostgreSQL test environment is unavailable. These tests require:
- Live PostgreSQL instance
- Async concurrency test infrastructure
- Database migration verification

**Rationale:** Windows Application Control blocks local PostgreSQL execution. Integration tests will be completed when PostgreSQL becomes available in CI, or when PR-1.2.5 auth APIs provide additional test infrastructure.

## EDD Compliance Status
- **SR-025 through SR-052**: Implemented and verified against the approved EDD.
- **SR-053 through SR-060**: Cannot be verified. Their authoritative source text is unavailable in the current EDD. No compliance claim is made for these requirements.

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
- [x] All deliverables checked in.
- [x] Unit validation gates passed.
- [ ] Integration tests deferred (PostgreSQL-dependent tests require live DB environment).
- [ ] Full SR-025–SR-060 verification pending availability of complete EDD source text for SR-053–SR-060.
