# PR-1.2.1 ADR Traceability

Status: closeout record for Authentication Foundation Milestone (PR-1.2.1 & PR-1.2.2)
Date: 2026-07-30

This document maps the implementation of the authentication foundation milestone (PR-1.2.1 and PR-1.2.2) to the frozen authentication ADRs.

| ADR | Implementation Status | Implemented Files | Deferred Work | Notes |
| --- | --- | --- | --- | --- |
| ADR-005 Authentication Core Principles | Partial | `apps/api/app/auth/context.py`, `apps/api/app/auth/service.py` | Middleware-generated request context, current-user/current-tenant resolvers | Platform auth remains separated from provider auth. Business-facing context object exists. |
| ADR-006 Authentication Service Principles | Partial | `apps/api/app/auth/service.py`, `apps/api/app/auth/sessions.py`, `apps/api/app/auth/tokens.py` | Complete middleware/API orchestration | `AuthenticationService` delegates to token/session services and does not implement OAuth or SQL. |
| ADR-007 External Identity Provider First | Deferred/Compliant | `apps/api/app/auth/service.py` | External IdP adapters, OAuth callback handling, identity linking | No username/password authentication exists. Provider work remains PR-1.3. |
| ADR-008 Token Strategy | Implemented | `apps/api/app/auth/tokens.py`, `apps/api/tests/test_tokens.py`, `apps/api/app/common/config.py` | Production key management and request-time session validation middleware | JWTs use minimal claims and no roles/permissions. Issuer/audience/required claims, algorithm allow-list, weak-secret rejection, and production default-secret rejection are implemented. Refresh tokens are opaque and hashed. |
| ADR-009 Session Principles | Partial | `apps/api/app/auth/sessions.py`, `apps/api/app/repositories/auth.py`, `apps/api/tests/test_sessions.py` | Session versioning, device-session model, session management APIs | Rotation, revocation, idle timeout, absolute timeout, remember-me duration, and reuse detection are represented. |
| ADR-010 Authentication Middleware Principles | Deferred | `apps/api/app/auth/context.py` | Authentication middleware, authorization middleware, `PolicyEngine`, dependency resolvers | Context object exists, runtime pipeline does not. |
| ADR-011 Security Principles | Partial | `apps/api/app/auth/exceptions.py`, `apps/api/app/auth/events.py`, `apps/api/app/auth/tokens.py`, `apps/api/app/auth/sessions.py`, `apps/api/app/common/config.py` | Key management provider and full tenant enforcement via middleware | Sensitive tokens are not logged. Errors are normalized. Session state is authoritative in service logic. PR-1.2.2 adds production rejection for unsafe JWT signing configuration. |
| ADR-012 Centralized Security Event Pipeline | Partial | `apps/api/app/auth/events.py`, `apps/api/tests/test_auth_events.py` | Audit-table sink, SIEM/export sinks, alerting | Event abstraction and structured logging sink exist. |
| ADR-013 Authentication API Principles | Deferred | None in PR-1.2.1 | `/auth/*` endpoints and response contracts | No API endpoints implemented in this foundation slice. |
| ADR-014 Capability Discovery Endpoint | Deferred | None in PR-1.2.1 | `GET /auth/capabilities` | Belongs with authentication API implementation. |

## Summary

PR-1.2.1 and PR-1.2.2 are aligned with the ADRs for their approved scope. Deferred items are expected and should be implemented in later PR-1.2 slices rather than expanded into this closeout.


