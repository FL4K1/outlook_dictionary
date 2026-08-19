# Roadmap

> Release-driven roadmap. Updated at each milestone transition.

---

## Completed Releases

- **v0.1.0 — Engineering Foundation**: Monorepo structure, FastAPI, Docker, CI/CD, Ruff, MyPy, Alembic.
- **v0.2.0 — Identity & Database**: Tenants, Users, Roles, Permissions, Sessions schema, async repositories.
- **v0.3.0-alpha.4 — Authentication APIs (PR-1.2.5)**: OAuth2-compatible token endpoint, refresh token rotation, session revocation, security event emission, request_id propagation.

---

## Current Release

### v0.3.0-alpha.4 — Authentication APIs (PR-1.2.5)
*(Released 2026-08-15)*
- OAuth2-compatible token endpoint (`POST /auth/token`).
- Refresh token rotation and revocation (`POST /auth/refresh`).
- Session revocation (`POST /auth/logout`, `POST /auth/logout-all`).
- Security event emission for all authentication outcomes.
- Request ID propagation into security events.

---

## Current Milestone (PR-1.2 Authentication)

### Current Sprint
**v0.3.0-alpha.5 — Provider Integration (PR-1.3)**
*Blocked — awaiting approved EDD*
- Microsoft Entra ID OAuth 2.0 / OIDC, identity linking, provider credentials.

### Future Sprints in Current Milestone
None — PR-1.2 milestone complete with PR-1.2.5 release.

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
