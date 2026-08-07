# Release History

> Permanent engineering history. Updated at each release.

---

## v0.1.0 — Engineering Foundation
**Major Features:**
- Monorepo structure, FastAPI application factory, Docker Compose infra.
- Shared packages: `mip_models`, `mip_providers`, `mip_ai`, `mip_email_parser`.
- CI/CD workflows, Ruff linting, MyPy, structlog, Alembic.
**Major Decisions:** Monorepo architecture, Protocol-based provider abstractions (PEP 544).
**Known Limitations:** None.
**Related ADRs:** ADR-001 through ADR-004.
**Related EDDs:** None.

---

## v0.2.0 — Identity & Database Foundation
**Major Features:**
- Organization, Tenant, User, Identity, Role, Permission, Session models.
- Async repository pattern, Alembic initial migration.
**Major Decisions:** Async SQLAlchemy, strict tenant isolation at the data model level.
**Known Limitations:** No runtime authentication or authorization logic.
**Related ADRs:** ADR-001 through ADR-004.
**Related EDDs:** None.

---

## v0.3.0-alpha.1 — Authentication Foundation & Token Infrastructure
*(Pending tag)*
**Major Features:**
- `AuthenticationContext`, `SecurityEvent`, `AuthenticationService`.
- `TokenService` (minimal JWT access tokens, opaque refresh tokens).
- `SigningProvider` protocol (HS256 implementation).
- JWT algorithm allow-list, production-safe signing secret validation, forbidden claim enforcement.
**Major Decisions:**
- Platform auth is independent from provider auth.
- Roles/permissions are NEVER stored in JWTs; authorization is server-side.
- Refresh tokens stored as SHA-256 hashes.
**Known Limitations:**
- Session rows act as refresh-token epochs (technical debt, to be resolved in PR-1.2.3).
- Security events log to structured logs only (no durable audit table yet).
**Related ADRs:** ADR-005 through ADR-014.
**Related EDDs:** `docs/reviews/PR-1.2.2-token-infrastructure-edd.md`.
