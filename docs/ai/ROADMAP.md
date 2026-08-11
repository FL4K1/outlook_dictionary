# Roadmap

> Release-driven roadmap. Updated at each milestone transition.

---

## Completed Releases

- **v0.1.0 — Engineering Foundation**: Monorepo structure, FastAPI, Docker, CI/CD, Ruff, MyPy, Alembic.
- **v0.2.0 — Identity & Database**: Tenants, Users, Roles, Permissions, Sessions schema, async repositories.

---

## Current Release

### v0.3.0-alpha.3 — Middleware & Authorization
*(Released 2026-08-11)*
- AuthenticationMiddleware — JWT verification, DeviceSession validation, context injection.
- Fail-closed middleware behavior — when the session factory is unavailable, protected routes return `401 Unauthorized`.
- PolicyEngine — default-deny authorization.
- Authorization dependencies — `require_permission()`, `require_role()`, `require_tenant_membership()`.
- Public route allow-list — explicit frozenset of exempt routes.
- Security events — authentication and authorization outcome emission.

---

## Current Milestone (PR-1.2 Authentication)

### Current Sprint
**v0.3.0-alpha.4 — Authentication APIs (PR-1.2.5)**
*Blocked — awaiting approved EDD*
- `/auth/*` endpoints (login, refresh, logout).

### Future Sprints in Current Milestone
**v0.3.0-alpha.5 — Provider Integration (PR-1.3)**
- Microsoft Entra ID OAuth 2.0 / OIDC, identity linking, provider credentials.

---

## Future Milestones

### v0.4.0-alpha.1 — Microsoft Entra ID Provider Integration
*Depends on: Authentication APIs (v0.3.0-alpha.4)*
- OAuth 2.0 / OIDC with Entra ID, identity linking, provider credentials.

### v0.5.0 — Mail Synchronization
*Depends on: Provider Integration (v0.4.0-alpha.1)*
- Graph API integration, delta sync, webhook notifications.

### v0.6.0 — Search Engine
*Depends on: Mail Synchronization (v0.5.0)*
- Elasticsearch indexing pipeline, structured/keyword search.

### v0.7.0 — Semantic Search
*Depends on: Search Engine (v0.6.0)*
- Embeddings, kNN vector search, hybrid retrieval.

### v0.8.0 — AI Reasoning Layer
*Depends on: Semantic Search (v0.7.0)*
- LLM integration, candidate reasoning, explainable answers.

### v0.9.0 — Administration & Frontend
*Depends on: AI Reasoning Layer (v0.8.0)*
- Tenant administration, User management, Next.js UI implementation.

### v1.0.0 — Production
*Depends on: Administration & Frontend (v0.9.0)*
- Security hardening, scaling, production infrastructure.
