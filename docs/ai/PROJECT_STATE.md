# Project State

> One-page engineering dashboard. Updated at each milestone transition.

---

## Current Status

| Field | Value |
| :--- | :--- |
| **Current Version** | v0.3.0-alpha.1 |
| **Current Branch** | main (pending release tag) |
| **Current Release** | v0.3.0-alpha.1 — Authentication Foundation & Token Infrastructure |
| **Current Sprint** | Session Infrastructure (PR-1.2.3) |
| **Current Milestone** | PR-1.2 Authentication |

---

## Subsystem State

### What is Completed
- **Engineering Foundation**: Monorepo, CI/CD, Docker, Ruff, MyPy, logging, Alembic
- **Identity & Database**: Organizations, tenants, users, roles, permissions, sessions schema
- **Authentication Foundation**: AuthenticationContext, SecurityEvent, AuthenticationService
- **Token Infrastructure**: TokenService, SigningProvider, JWT claim enforcement

### What is In Progress
- **Session Infrastructure**: Stable device-session model (Kickoff phase)

### What is Blocked
- MyPy cannot execute locally due to Windows Application Control. (Must pass in CI).
- Session rows double as refresh-token epochs (Technical Debt blocking clean rotation until PR-1.2.3).

### What Comes Next
- **Middleware & Authorization**: Depends on Session Infrastructure
- **Authentication APIs**: Depends on Middleware
- **Provider Integration**: Microsoft Entra ID — depends on Auth APIs

---

## Planning

| Field | Value |
| :--- | :--- |
| **Next Milestone** | v0.3.0-alpha.2 — Session Infrastructure |
| **Next Branch** | `feature/pr-1.2.3-session-infrastructure` |
| **Next Threat Model** | Session Security Threat Model (before PR-1.2.3 implementation) |
| **Next EDD** | `docs/reviews/PR-1.2.3-session-infrastructure-edd.md` |
| **Last Updated** | 2026-08-07 |
