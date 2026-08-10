# Project State

> One-page engineering dashboard. Updated at each milestone transition.

---

## Current Status

| Field | Value |
| :--- | :--- |
| **Current Version** | v0.3.0-alpha.2 |
| **Current Branch** | feature/pr-1.2.4-middleware-authorization |
| **Current Release** | v0.3.0-alpha.2 — Session Infrastructure (PR-1.2.3) |
| **Current Sprint** | Middleware & Authorization (PR-1.2.4) — Complete, Conditional Merge Readiness |
| **Current Milestone** | PR-1.2 Authentication |

---

## Subsystem State

### What is Completed
- **Engineering Foundation**: Monorepo, CI/CD, Docker, Ruff, MyPy, logging, Alembic
- **Identity & Database**: Organizations, tenants, users, roles, permissions, sessions schema
- **Authentication Foundation**: AuthenticationContext, SecurityEvent, AuthenticationService
- **Token Infrastructure**: TokenService, SigningProvider, JWT claim enforcement
- **Session Infrastructure**: Stable DeviceSession model, RefreshTokenFamily epochs, atomic refresh with locking, timeout enforcement, revocation, security event emission
- **Middleware & Authorization**: AuthenticationMiddleware, fail-closed factory=None, JWT sid/tid/oid enforcement, DeviceSession validation, tenant/org isolation, membership validation, immutable AuthenticationContext, PolicyEngine with default-deny, authorization dependencies, public route allow-list, authorization security events

### What is In Progress
- None

### What is Blocked
- **SR-053–SR-060 Verification**: Approved PR-1.2.4 EDD covers SR-025 through SR-052. Requirements SR-053 through SR-060 cannot be verified because their authoritative source text is unavailable. This prevents a full EDD compliance claim but does not block the verified implementation.
- **MyPy**: Local execution blocked by Windows Application Control. (Must pass in CI).
- **PostgreSQL-backed integration tests**: Deferred (requires live DB environment — not available locally).

### What Comes Next
- **Authentication APIs (PR-1.2.5)**: Depends on Middleware & Authorization
- **Provider Integration**: Microsoft Entra ID — depends on Auth APIs

---

## Planning

| Field | Value |
| :--- | :--- |
| **Next Milestone** | v0.3.0-alpha.3 — Middleware & Authorization |
| **Next Branch** | `feature/pr-1.2.4-middleware-authorization` (pending merge) |
| **Next Threat Model** | PR-1.2.4 Middleware & Authorization security requirements (complete) |
| **Next EDD** | `docs/reviews/PR-1.2.4-middleware-authorization-edd.md` (present; SR-025–SR-052 verified) |
| **Last Updated** | 2026-08-09 |
