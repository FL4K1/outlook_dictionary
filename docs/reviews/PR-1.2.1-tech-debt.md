# PR-1.2.1 Technical Debt Register

Status: closeout record for Authentication Foundation Milestone (PR-1.2.1 & PR-1.2.2)
Date: 2026-07-30

This document records intentional limitations in the Authentication Foundation slice (PR-1.2.1 & PR-1.2.2). These are not merge blockers because the implementation is scoped to authentication foundations, not the complete PR-1.2 authentication subsystem.

## 1. Session Rows Represent Refresh-Token Epochs

Description:
Refresh rotation currently revokes the previous `Session` row and creates a new row with the new refresh-token hash. This preserves consumed refresh-token hashes so replay/reuse can be detected.

Why It Exists:
The existing schema has a single `refresh_token_hash` on `sessions` and no separate refresh-token history table. Keeping revoked session rows provides replay detection without expanding the schema in PR-1.2.1.

Architectural Impact:
This is compliant with refresh-token rotation and reuse detection, but it is not the final ideal representation for user-visible device sessions. A future device-session model may separate stable device sessions from refresh-token epochs.

Severity:
Medium.

Planned Future PR:
PR-1.2.3 Session Infrastructure.

## 2. Security Events Log Only

Description:
`SecurityEventEmitter` currently emits structured security events to application logs only.

Why It Exists:
PR-1.2.1 establishes the centralized event abstraction. Durable audit persistence and SIEM/export sinks belong to later authentication/security slices.

Architectural Impact:
The event pipeline shape is correct, but audit-table persistence is not yet implemented.

Severity:
Medium.

Planned Future PR:
PR-1.2.5 Authentication APIs and audit integration, or a dedicated audit hardening slice.

## 3. Production Secret Validation Is Enforced

Status:
Resolved in this milestone.

Description:
`jwt_signing_secret` still has a development default for local use, but production settings now reject that default and reject HS256 secrets shorter than 32 bytes.

Architectural Impact:
Token configuration now fails closed for the known unsafe production cases in this milestone.

Severity:
Resolved for current scope.

Planned Future PR:
None for this limitation. External secret retrieval and rotation remain covered by the key-management limitation below.

## 4. Key Management Provider Is Not Fully Implemented

Description:
`SigningProvider` exists for JWT signing/verification, but a full `KeyManagementProvider` abstraction is not yet implemented.

Why It Exists:
PR-1.2.1 needed to remove hardcoded algorithm coupling and establish an interface boundary. Full key retrieval/rotation is a larger token-infrastructure concern.

Architectural Impact:
The implementation remains extensible, but production-grade key rotation and external secret storage are deferred.

Severity:
Medium.

Planned Future PR:
Future key-management hardening slice.

## 5. AuthenticationContext Is Not Yet Middleware-Generated

Description:
`AuthenticationContext` is implemented and tested, but no middleware currently constructs it for live requests.

Why It Exists:
PR-1.2.1 is the foundation slice. Runtime middleware is intentionally deferred.

Architectural Impact:
Business services do not yet receive `AuthenticationContext` automatically. This is expected until middleware and dependency resolvers are implemented.

Severity:
Medium.

Planned Future PR:
PR-1.2.4 Middleware and Context Resolution.

## 6. Authorization/Policy Engine Not Implemented

Description:
No `PolicyEngine` or authorization resolver exists yet.

Why It Exists:
PR-1.2.1 focuses on authentication primitives, token basics, session basics, and security events.

Architectural Impact:
Server-side permission resolution is represented in `AuthenticationContext`, but not yet populated by runtime authorization code.

Severity:
Medium.

Planned Future PR:
PR-1.2.4 Middleware and Authorization.

## 7. Auth API Surface Not Implemented

Description:
No `/auth/*` endpoints are implemented in this slice.

Why It Exists:
The current slice is foundational and intentionally avoids adding API behavior before token/session internals are fully stable.

Architectural Impact:
No runtime user-facing authentication flow exists yet.

Severity:
Medium.

Planned Future PR:
PR-1.2.5 Authentication APIs.

## 8. Mypy Is Blocked By Local Application Control

Description:
Focused mypy validation could not run because Windows Application Control blocked the local mypy DLL in the virtual environment.

Why It Exists:
This is a host/tooling policy issue rather than a code issue.

Architectural Impact:
Type-safety confidence is reduced until mypy can be run in a working environment.

Severity:
Medium for merge validation.

Planned Future PR:
Before merging PR-1.2.1, or in CI if local execution remains blocked.

