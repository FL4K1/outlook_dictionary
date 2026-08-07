# Current Sprint

> This file describes ONLY the active engineering sprint. Update at each sprint transition.

---

## Sprint: Session Infrastructure (PR-1.2.3)
**Status**: Kickoff — Design phase.

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
- **Threat Model**: Has not yet been produced. Must be the first design activity.
- **MyPy**: Local execution blocked by Windows Application Control.

---

## Required Documents Before Implementation
- [ ] **Required Threat Model**: Session Security Threat Model.
- [ ] **Required EDD**: Engineering Design Document (`docs/reviews/PR-1.2.3-session-infrastructure-edd.md`).
- [ ] **Required Implementation Contract**: Approved boundaries and interfaces.

## Deliverables
- [ ] `DeviceSession` SQLAlchemy model.
- [ ] Refresh-token family abstraction.
- [ ] Alembic migration for updated schema.
- [ ] Updated `SessionRepository` and `SessionService`.
- [ ] Session validation and timeout logic.
- [ ] Unit and Integration tests.
- [ ] Updated Technical Debt Register and CHANGELOG.

## Validation Gates
- [ ] 100% test pass rate.
- [ ] `ruff check .` passes.
- [ ] `ruff format --check .` passes.
- [ ] `mypy` passes (in CI or locally).
- [ ] `alembic upgrade head` and `alembic downgrade base` succeed.

## Definition of Done
- [ ] Session identity is fully decoupled from refresh-token rotation events.
- [ ] Timeout enforcement and multi-device tracking are functional.
- [ ] All deliverables are checked in.
- [ ] All validation gates are passed.
