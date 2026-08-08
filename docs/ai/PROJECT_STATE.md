# Project State

> One-page engineering dashboard. Updated at each milestone transition.

---

## Current Status

| Field | Value |
| :--- | :--- |
| **Current Version** | v0.3.0-alpha.2 |
| **Current Branch** | main (pending release tag) |
| **Current Release** | v0.3.0-alpha.2 — Session Infrastructure |
| **Current Sprint** | Session Infrastructure (PR-1.2.3) |
| **Current Milestone** | PR-1.2 Authentication |

---

## Subsystem State

### What is Completed
- **Engineering Foundation**: Monorepo, CI/CD, Docker, Ruff, MyPy, logging, Alembic
- **Identity & Database**: Organizations, tenants, users, roles, permissions, sessions schema
- **Authentication Foundation**: AuthenticationContext, SecurityEvent, AuthenticationService
- **Token Infrastructure**: TokenService, SigningProvider, JWT claim enforcement
- **Session Infrastructure**: Stable DeviceSession model, RefreshTokenFamily epochs, atomic refresh with locking, timeout enforcement, revocation, security event emission

### What is In Progress
- None

### What is Blocked
- MyPy cannot execute locally due to Windows Application Control. (Must pass in CI).
- PostgreSQL-backed integration tests deferred (requires live DB environment — not available locally).

### What Comes Next
- **Middleware & Authorization**: Depends on Session Infrastructure
- **Authentication APIs**: Depends on Middleware
- **Provider Integration**: Microsoft Entra ID — depends on Auth APIs

---

## Planning

| Field | Value |
| :--- | :--- |
| **Next Milestone** | v0.3.0-alpha.3 — Middleware & Authorization |
| **Next Branch** | `feature/pr-1.2.4-middleware-authorization` |
| **Next Threat Model** | Middleware & Authorization security requirements (PR-1.2.4) |
| **Next EDD** | `docs/reviews/PR-1.2.4-middleware-authorization-edd.md` (pending) |
| **Last Updated** | 2026-08-08 |
