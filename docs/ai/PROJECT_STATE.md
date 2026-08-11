# Project State

> One-page engineering dashboard. Updated at each milestone transition.

---

## Current Status

| Field | Value |
| :--- | :--- |
| **Current Version** | v0.3.0-alpha.3 |
| **Current Branch** | main |
| **Current Release** | v0.3.0-alpha.3 — Middleware & Authorization (PR-1.2.4) |
| **Current Sprint** | Authentication APIs (PR-1.2.5) — Blocked (awaiting EDD) |
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
- **Release**: v0.3.0-alpha.3 merged to main

### What is In Progress
- None

### What is Blocked
- **PR-1.2.5 EDD**: The approved Engineering Design Document for Authentication APIs is not present in the repository. Implementation cannot begin without it.
- **SR-053–SR-060 Verification**: Approved PR-1.2.4 EDD covers SR-025 through SR-052. Requirements SR-053 through SR-060 cannot be verified because their authoritative source text is unavailable. This prevents full EDD compliance claims.
- **MyPy**: Local execution blocked by Windows Application Control. (Must pass in CI).
- **PostgreSQL-backed integration tests**: Deferred (requires live DB environment — not available locally).

### What Comes Next
- **Authentication APIs (PR-1.2.5)**: Depends on approved EDD. Blocked until EDD is provided.
- **Provider Integration**: Microsoft Entra ID — depends on Auth APIs

---

## Planning

| Field | Value |
| :--- | :--- |
| **Next Milestone** | v0.3.0-alpha.4 — Authentication APIs (PR-1.2.5) |
| **Next Branch** | `feature/pr-1.2.5-authentication-apis` (blocked — awaiting EDD) |
| **Next Threat Model** | PR-1.2.5 Authentication APIs security requirements (blocked) |
| **Next EDD** | `docs/reviews/PR-1.2.5-authentication-apis-edd.md` (NOT PRESENT) |
| **Last Updated** | 2026-08-11 |
