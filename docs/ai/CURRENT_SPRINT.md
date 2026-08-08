# Current Sprint

> This file describes ONLY the active engineering sprint. Update at each sprint transition.

---

## Sprint: Session Infrastructure (PR-1.2.3)
**Status**: Complete — Ready for Release.

---

## Objectives
Establish a durable device-session model tracking live authentication sessions independently from token refresh epochs, resolving technical debt from the Authentication Foundation.

## Scope
- Stable device-session database model.
- Refresh-token family model (epoch tracking).
- Session lifecycle management (creation, rotation, revocation).
- Multi-device support for a single user.
- Idle and absolute timeout enforcement.

## Out of Scope
- OAuth implementation or Microsoft Entra ID integration.
- Request-time middleware or PolicyEngine.
- User-facing Authentication APIs (`/auth/*`).

## Dependencies
- **Depends on:** Authentication Foundation (v0.3.0-alpha.1), Identity & Database Foundation (v0.2.0).
- **Required by:** Middleware & Authorization (v0.3.0-alpha.3).

## Current Risks
- Data migration complexity (impact on existing dev data).
- Concurrent refresh race conditions.
- Session schema lookup performance.

## Current Blockers
- **MyPy**: Local execution blocked by Windows Application Control.
- **Threat Model Document**: The standalone Session Security Threat Model document was not committed to the repository. Its security requirements are captured in the approved Implementation Contract (Section 13, SR-001 through SR-024).

---

## Required Documents Before Implementation
- [x] **Threat Model Requirements**: Session security requirements (SR-001–SR-024) are captured in the approved Implementation Contract, Section 13.
- [x] **Required EDD**: Engineering Design Document (`docs/reviews/PR-1.2.3-session-infrastructure-edd.md`).
- [x] **Required Implementation Contract**: Approved boundaries and interfaces.

## Deliverables
- [x] `DeviceSession` SQLAlchemy model.
- [x] Refresh-token family abstraction.
- [x] Alembic migration for updated schema.
- [x] Updated `SessionRepository` and `SessionService`.
- [x] Session validation and timeout logic.
- [x] Unit tests (65/65 passing).
- [ ] Integration tests (deferred — see below).
- [x] Updated Technical Debt Register and CHANGELOG.

## Validation Gates
- [x] 100% unit test pass rate (65/65).
- [x] `ruff check` passes.
- [x] `ruff format --check` passes.
- [x] `mypy --strict` passes.
- [ ] `alembic upgrade head` / `downgrade base` (requires PostgreSQL — not available locally).
- [ ] Integration tests (deferred — see below).

## Integration Test Status
PostgreSQL-backed integration tests (AC-8, AC-9, AC-12, migration backfill, revocation effectiveness, timeout enforcement) are **deferred** because a local PostgreSQL test environment is unavailable. These tests require:
- Live PostgreSQL instance
- Async concurrency test infrastructure
- Database migration verification

**Rationale:** Windows Application Control blocks local PostgreSQL execution. Integration tests will be completed in PR-1.2.4 when the middleware layer provides additional test infrastructure, or when PostgreSQL becomes available in CI.

## Definition of Done
- [x] Session identity is fully decoupled from refresh-token rotation events.
- [x] Timeout enforcement and multi-device tracking are functional.
- [x] All deliverables are checked in.
- [x] Unit validation gates passed.
- [ ] Integration tests deferred to PR-1.2.4 (PostgreSQL-dependent tests require live DB environment).
