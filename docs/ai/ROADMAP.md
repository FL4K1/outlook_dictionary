# Roadmap

> Release-driven roadmap. Updated at each milestone transition.

---

## Completed Releases

- **v0.1.0 — Engineering Foundation**: Monorepo structure, FastAPI, Docker, CI/CD, Ruff, MyPy, Alembic.
- **v0.2.0 — Identity & Database**: Tenants, Users, Roles, Permissions, Sessions schema, async repositories.

---

## Current Release

### v0.3.0-alpha.1 — Authentication Foundation & Token Infrastructure
*(Currently on `main`, pending release tag)*
- AuthenticationContext, SecurityEvent, AuthenticationService.
- TokenService, SigningProvider (HS256), JWT claim enforcement.
- SessionService, refresh-token rotation, reuse detection.

---

## Current Milestone (PR-1.2 Authentication)

### Current Sprint
**v0.3.0-alpha.2 — Session Infrastructure (Next)**
- Stable device-session model.
- Session lifecycle, versioning, revocation.
- Idle/absolute timeouts, multi-device support.

### Future Sprints in Current Milestone
**v0.3.0-alpha.3 — Authentication Middleware & Authorization**
- Middleware, PolicyEngine, Context injection.

**v0.3.0-alpha.4 — Authentication APIs**
- `/auth/*` endpoints (login, refresh, logout).

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
