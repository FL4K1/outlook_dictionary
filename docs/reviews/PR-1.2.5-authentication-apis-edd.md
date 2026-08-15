# PR-1.2.5 Authentication APIs — Engineering Design Document

**Status:** APPROVED  
**Date:** 2026-08-15  
**Branch:** feature/pr-1.2.5-authentication-apis  
**Base Release:** v0.3.0-alpha.3  
**Scope:** PR-1.2.5 Authentication APIs  
**Authoritative Security Baseline:** PR-1.2.4 Security Threat Model (approved)

---

## 1. Executive Summary

PR-1.2.4 established the HTTP-boundary authentication enforcement layer: JWT validation, DeviceSession state checks, AuthenticationContext hydration, and PolicyEngine authorization. However, the API surface for obtaining and managing authentication credentials remains unimplemented.

PR-1.2.5 closes this gap by introducing the authentication API endpoints that allow clients to:
- Refresh access tokens using opaque refresh tokens
- Issue new token pairs via the OAuth2-compatible token endpoint (refresh_token grant only)
- Revoke individual refresh tokens
- Revoke all refresh tokens for a session

### Architectural Outcome

The authentication flow becomes:

```
CLIENT
  │
  ▼
POST /auth/refresh (public)
  │
  ▼
SessionService.refresh_session()
  │
  ▼
TokenService.create_access_token()
  │
  ▼
TokenResponse (access_token + refresh_token)
```

All identity and session state is server-resolved. The stable DeviceSession architecture from PR-1.2.3 is preserved. No client-supplied claims are trusted.

### Security Outcome

PR-1.2.5 enables the complete authentication lifecycle while preserving all PR-1.2.3 and PR-1.2.4 security invariants:
- Refresh token rotation via RefreshTokenFamily epoch
- DeviceSession identity stability
- Idle/absolute timeout enforcement
- Refresh token reuse detection (SESSION_REUSE_DETECTED + ALL_SESSIONS_REVOKED)
- Tenant isolation at the API boundary

---

## 2. Scope

### In Scope

| Component | Description |
| :--- | :--- |
| POST /auth/refresh | Exchange valid refresh token for new access token + refresh token pair |
| POST /auth/token | OAuth2-compatible token endpoint (grant_type=refresh_token only) |
| POST /auth/logout | Revoke a single refresh token |
| POST /auth/logout-all | Revoke all refresh tokens for a session |
| Public route registration | All four endpoints registered as public routes |
| Schemas | Pydantic request/response models for all endpoints |
| Security events | Preserve existing SessionService event emission |
| Tests | Unit tests for all endpoints and schemas |

### Out of Scope

| Component | Reason | Future Milestone |
| :--- | :--- | :--- |
| Username/password authentication | Not in PR-1.2.5 scope | PR-1.3+ |
| grant_type=password | Not in PR-1.2.5 scope | PR-1.3+ |
| OAuth provider flows | Not in PR-1.2.5 scope | PR-1.3+ |
| MFA/TOTP | Not in PR-1.2.5 scope | PR-1.3+ |
| Rate limiting | Not required for initial implementation | PR-1.2.5+ |
| Service-to-service auth | Not required for initial implementation | PR-1.2.5+ |
| Redis | No caching in PR-1.2.5 | PR-1.2.5+ |
| Access-token blacklist | Not required for initial implementation | PR-1.2.5+ |
| Frontend auth UX | Next.js is separate | PR-1.9+ |
| Database schema changes | No schema changes in PR-1.2.5 | N/A |

---

## 3. Architecture

### Target Request Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENT (UNTRUSTED)                              │
│                                                                         │
│  Sends: POST /auth/refresh                                              │
│  Body: { "refresh_token": "<opaque-token>" }                            │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      FASTAPI MIDDLEWARE STACK                            │
│                                                                         │
│  Public route — no AuthenticationMiddleware required                    │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        ROUTER / ENDPOINT                                 │
│                                                                         │
│  POST /auth/refresh                                                     │
│  1. Extract ip_address, user_agent from request                         │
│  2. Construct services (TokenService, SessionService, repositories)     │
│  3. Call SessionService.refresh_session()                               │
│  4. Resolve tenant via TenantRepository                                 │
│  5. Create access token via TokenService                                │
│  6. Return TokenResponse                                                │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      SERVICE LAYER                                       │
│                                                                         │
│  SessionService.refresh_session():                                      │
│  - Validate refresh token                                               │
│  - Detect reuse (RefreshTokenReusedError)                               │
│  - Check DeviceSession state (revoked, expired, idle timeout)           │
│  - Rotate refresh token (RefreshTokenFamily epoch)                      │
│  - Emit security events                                                 │
│  - Return (session, new_refresh_token)                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    REPOSITORY LAYER                                      │
│                                                                         │
│  - DeviceSessionRepository: session state lookup/update                 │
│  - RefreshTokenFamilyRepository: token hash storage, epoch management   │
│  - TenantRepository: tenant existence/active validation                 │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATABASE                                         │
│                                                                         │
│  PostgreSQL — source of truth for sessions, tokens, tenants             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Responsibility Matrix

| Layer | Responsibility | Must Not |
| :--- | :--- | :--- |
| Router | Extract request metadata, orchestrate service calls, construct response | Implement business logic, bypass services |
| SessionService | Refresh session, rotate tokens, detect reuse, emit events | Create access tokens, resolve tenants |
| TokenService | Create/verify JWTs, manage signing | Access database, validate sessions |
| Repositories | Data access with tenant isolation | Bypass tenant filters, accept client-supplied IDs |

---

## 4. API Endpoints

### 4.1 POST /auth/refresh

**Purpose:** Exchange a valid refresh token for a new access token and refresh token pair.

**Authentication:** Public (no JWT required)

**Request Schema:**

```python
class RefreshTokenRequest(BaseModel):
    refresh_token: str
```

**Response Schema:**

```python
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
```

**Handler Flow:**

1. Extract `ip_address` from `request.client.host`
2. Extract `user_agent` from `request.headers.get("user-agent")`
3. Construct `TokenService(settings)`
4. Construct `DeviceSessionRepository(db)`
5. Construct `RefreshTokenFamilyRepository(db)`
6. Construct `SessionService(device_session_repo, refresh_token_family_repo, token_service, settings)`
7. Call `session_service.refresh_session(plaintext_refresh_token, ip_address, user_agent)` — exactly once
8. Construct `TenantRepository(db)`
9. Call `tenant_repo.get(session.tenant_id)`
10. If tenant is None or not active → raise `TenantAccessDeniedError`
11. Call `token_service.create_access_token(AccessTokenSubject(user_id, tenant_id, organization_id, session_id))`
12. Return `TokenResponse(access_token, new_refresh_token.plaintext, "bearer", expires_in)`

**Transaction Behavior:**

- Uses existing `get_db()` dependency which yields a single `AsyncSession`
- `SessionService.refresh_session()` runs inside the caller-managed transaction
- If tenant lookup fails after refresh succeeds, the exception propagates and `get_db()`'s existing rollback behavior handles cleanup
- No commit/rollback logic added to the handler

### 4.2 POST /auth/token

**Purpose:** OAuth2-compatible token endpoint.

**Authentication:** Public

**Request Schema:**

```python
class TokenRequest(BaseModel):
    grant_type: str
    refresh_token: str
    scope: str | None = None
```

**Response Schema:**

```python
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
```

**Grant Type Validation:**

- `grant_type` is accepted as a plain `str` at the Pydantic schema layer.
- The endpoint performs semantic grant-type validation.
- `grant_type == "refresh_token"` proceeds to refresh.
- Any other `grant_type` value (including `password`) reaches the endpoint logic and returns HTTP 400 OAuth2 `unsupported_grant_type`.
- This is intentional: using `Literal["refresh_token"]` would cause Pydantic/FastAPI to reject unsupported grant types at schema validation with HTTP 422, which would violate the approved OAuth2 contract.
- Missing `grant_type` remains a Pydantic/schema failure → HTTP 422 standard `ErrorResponse`.

**Handler Flow:**

1. Validate `grant_type`:
   - If missing or not `"refresh_token"` → return HTTP 400 OAuth2 `unsupported_grant_type`.
2. Validate `refresh_token`:
   - If present but empty string → return HTTP 400 OAuth2 `invalid_request`.
   - If missing → FastAPI/Pydantic returns HTTP 422 standard `ErrorResponse` before handler execution.
3. Delegate to the same internal refresh orchestration as `POST /auth/refresh`:
   - Extract `ip_address` and `user_agent` from request.
   - Construct `TokenService`, `DeviceSessionRepository`, `RefreshTokenFamilyRepository`, `SessionService`, `TenantRepository`.
   - Call `session_service.refresh_session(plaintext_refresh_token, ip_address, user_agent)` exactly once.
   - Resolve `organization_id` from `session.tenant_id` via `TenantRepository`.
   - Call `token_service.create_access_token(AccessTokenSubject(...))`.
4. Return `TokenResponse` on success.
5. On domain exceptions, catch and return OAuth2-formatted responses (see Section 5).

**Transaction Behavior:**

- Same as `POST /auth/refresh`: uses `get_db()` dependency; `SessionService.refresh_session()` runs inside the caller-managed transaction.

**OAuth2 Error Behavior:**

- Pydantic/schema validation failures occur before handler execution and return HTTP 422 in the standard `ErrorResponse` format.
- Semantic parameter errors and business logic failures caught inside the handler return OAuth2-formatted JSON with the error codes documented in Section 5.
- 401 responses include the header: `WWW-Authenticate: Bearer realm="platform"`.

**Security Events:**

- Same as `POST /auth/refresh`. The handler does not emit duplicate events; `SessionService.refresh_session()` owns event emission.

### 4.3 POST /auth/logout

**Purpose:** Revoke a single refresh token.

**Authentication:** Public

**Request Schema:**

```python
class LogoutRequest(BaseModel):
    refresh_token: str
```

**Response:** HTTP 204 No Content

**Phase 1 Behavior:** Returns HTTP 501 Not Implemented. Full implementation deferred to Phase 4.

### 4.4 POST /auth/logout-all

**Purpose:** Revoke all refresh tokens for a session.

**Authentication:** Public

**Request Schema:**

```python
class LogoutAllRequest(BaseModel):
    refresh_token: str
```

**Response:** HTTP 204 No Content

**Phase 1 Behavior:** Returns HTTP 501 Not Implemented. Full implementation deferred to Phase 4.

---

## 5. Error Handling

### Error Contract

All errors use the existing `ErrorResponse` format:

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<human-readable message>",
    "request_id": "<request-id>"
  }
}
```

### Failure Mapping

| Scenario | HTTP Status | Error Code | Security Event |
| :--- | :--- | :--- | :--- |
| Missing/structurally invalid request fields | 422 | VALIDATION_ERROR | None |
| Invalid refresh token | 401 | TOKEN_INVALID | TOKEN_REFRESH_FAILED |
| Refresh token reuse detected (including revoked DeviceSession) | 401 | REFRESH_TOKEN_REUSED | SESSION_REUSE_DETECTED → ALL_SESSIONS_REVOKED |
| Session absolute timeout exceeded | 401 | SESSION_EXPIRED | SESSION_EXPIRED |
| Session idle timeout exceeded | 401 | SESSION_EXPIRED | SESSION_EXPIRED |
| Tenant not found or inactive | 403 | TENANT_ACCESS_DENIED | None |

### Revoked Session Behavior

A refresh token bound to a revoked `DeviceSession` is treated by the existing `SessionService` as a refresh-token reuse/compromise condition. The existing `SessionService.refresh_session()` raises `RefreshTokenReusedError`, which maps to:

- HTTP 401
- Error code: `REFRESH_TOKEN_REUSED`
- Security events:
  1. `SESSION_REUSE_DETECTED` (outcome: FAILURE, reason: "Revoked session refresh token was presented")
  2. `ALL_SESSIONS_REVOKED` (outcome: SUCCESS, reason: "Compromised refresh token detected")

This preserves the existing PR-1.2.3 security invariant: a single failure mode (`RefreshTokenReusedError`) covers both token replay attacks and revoked-session token presentations.

### Information Disclosure Policy

- 401 responses MUST NOT reveal whether a user exists, whether a session exists, or which specific check failed
- 403 responses MUST NOT reveal tenant existence beyond the access denial
- Security events contain full details but are logged server-side only

### OAuth2 Error Format (POST /auth/token only)

`POST /auth/token` returns OAuth2-formatted error responses for semantic parameter errors and business logic failures caught inside the handler. Pydantic validation errors occur before handler execution and return the standard `ErrorResponse` format (HTTP 422).

**OAuth2 error response format:**

```json
{
  "error": "invalid_grant",
  "error_description": "The refresh token is invalid."
}
```

**Validation-layer behavior:**

| Validation Layer | HTTP Status | Format | Example |
| :--- | :--- | :--- | :--- |
| Pydantic/schema validation | 422 | Standard `ErrorResponse` | Missing `refresh_token` field |
| Semantic parameter error (inside handler) | 400 | OAuth2 error | `grant_type=refresh_token` but `refresh_token` is empty string |
| Unsupported `grant_type` | 400 | OAuth2 `unsupported_grant_type` | `grant_type=password` |
| Business logic failure | 401 | OAuth2 `invalid_grant` | Invalid/consumed/expired refresh token |

**Exception mapping for `POST /auth/token`:**

| Domain Exception | OAuth2 Error | HTTP Status |
| :--- | :--- | :--- |
| `TokenInvalidError` | `invalid_grant` | 401 |
| `RefreshTokenReusedError` | `invalid_grant` | 401 |
| `SessionExpiredError` | `invalid_grant` | 401 |
| Unsupported `grant_type` | `unsupported_grant_type` | 400 |
| Semantic invalid request (e.g., empty `refresh_token`) | `invalid_request` | 400 |

**WWW-Authenticate header:**

All HTTP 401 responses from `POST /auth/token` MUST include the header:

```
WWW-Authenticate: Bearer realm="platform"
```

**Implementation note:** The `/auth/token` endpoint handler catches domain exceptions and returns OAuth2-formatted JSON directly. Pydantic validation errors occur before endpoint business logic executes and therefore return 422 in the standard API format via the global exception handler.

**Revoked DeviceSession behavior on `/auth/token`:**

A refresh token bound to a revoked `DeviceSession` triggers the existing `RefreshTokenReusedError` from `SessionService.refresh_session()`. The `/auth/token` handler maps this to HTTP 401 with OAuth2 error `invalid_grant`. The existing security event sequence (`SESSION_REUSE_DETECTED` followed by `ALL_SESSIONS_REVOKED`) is preserved and emitted by `SessionService`, not duplicated by the handler.

---

## 6. Security Events

### Required Events

| Event Type | Trigger | Required Fields | Prohibited Fields |
| :--- | :--- | :--- | :--- |
| TOKEN_REFRESHED | Successful refresh | user_id, tenant_id, session_id, ip_address, user_agent, request_id | token, refresh_token, jti |
| TOKEN_REFRESH_FAILED | Invalid refresh token | reason, ip_address, user_agent, request_id | token, user_id |
| SESSION_REUSE_DETECTED | Reused refresh token (including revoked DeviceSession) | session_id, reason | token, user_id |
| ALL_SESSIONS_REVOKED | Bulk revocation triggered by reuse detection | user_id, tenant_id, reason | token, session_details |
| SESSION_EXPIRED | Absolute/idle timeout exceeded | session_id, reason | token, user_id |

### Event Ownership

| Event | Emitted By |
| :--- | :--- |
| TOKEN_REFRESHED | SessionService (existing) |
| TOKEN_REFRESH_FAILED | SessionService (existing) |
| SESSION_REUSE_DETECTED | SessionService (existing) |
| ALL_SESSIONS_REVOKED | SessionService (existing) |
| SESSION_EXPIRED | SessionService (existing) |

### Correlation

- All events include `request_id` from RequestIdMiddleware
- All events include UTC timestamp
- All events include `SecurityOutcome.SUCCESS` or `SecurityOutcome.FAILURE`

---

## 7. Schemas

### Request Schemas

```python
class RefreshTokenRequest(BaseModel):
    refresh_token: str

class TokenRequest(BaseModel):
    grant_type: str
    refresh_token: str
    scope: str | None = None

class LogoutRequest(BaseModel):
    refresh_token: str

class LogoutAllRequest(BaseModel):
    refresh_token: str
```

### Response Schemas

```python
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
```

### Validation Semantics

- Missing or structurally invalid fields → HTTP 422 via FastAPI/Pydantic's default validation pipeline
- No custom validators that alter 422 behavior
- No raw tokens logged or exposed in validation errors

---

## 8. Public Route Registration

All four authentication endpoints are registered as public routes:

| Method | Path | Public |
| :--- | :--- | :--- |
| POST | /auth/token | Yes |
| POST | /auth/refresh | Yes |
| POST | /auth/logout | Yes |
| POST | /auth/logout-all | Yes |

Public route enforcement preserves the default-deny philosophy: routes without explicit authentication dependencies are public, but all other routes require authentication.

---

## 9. Security Requirements Traceability

### PR125-SR-001: Token Refresh

**EDD Component:** POST /auth/refresh  
**Implementation:** Router orchestrates SessionService and TokenService to exchange refresh token for new access token  
**Test:** Unit test: valid refresh token returns 200 with new tokens

### PR125-SR-002: Revoked Session Detection

**EDD Component:** POST /auth/refresh, SessionService  
**Implementation:** A refresh token presented for a revoked DeviceSession is treated as a refresh-token reuse/compromise condition. The existing SessionService raises `RefreshTokenReusedError`, which maps to HTTP 401 with error code `REFRESH_TOKEN_REUSED`. This preserves the existing security event sequence: `SESSION_REUSE_DETECTED` followed by `ALL_SESSIONS_REVOKED`.  
**Test:** Unit test: revoked session refresh token returns 401 REFRESH_TOKEN_REUSED

### PR125-SR-003: Tenant Validation

**EDD Component:** POST /auth/refresh, TenantRepository  
**Implementation:** After successful session refresh, resolve tenant via `session.tenant_id`. If tenant is missing or inactive, raise `TenantAccessDeniedError` (403).  
**Test:** Unit test: missing tenant returns 403; inactive tenant returns 403

### PR125-SR-004: Access Token Subject Integrity

**EDD Component:** TokenService.create_access_token()  
**Implementation:** AccessTokenSubject constructed entirely from server-side session and tenant data: user_id, tenant_id, organization_id, session_id. No client input used.  
**Test:** Unit test: subject fields match session/tenant data

### PR125-SR-005: Refresh Token Rotation

**EDD Component:** SessionService, RefreshTokenFamily  
**Implementation:** Existing RefreshTokenFamily epoch rotation preserved. New refresh token issued, old token marked consumed.  
**Test:** Existing PR-1.2.3 tests cover rotation; Phase 2 tests verify new token returned

### PR125-SR-006: Session Identity Stability

**EDD Component:** DeviceSession  
**Implementation:** DeviceSession.id remains stable across refresh operations. SessionService updates existing DeviceSession in-place.  
**Test:** Unit test: session_id in access token subject matches original session.id

### PR125-SR-007: Expires In Calculation

**EDD Component:** TokenResponse  
**Implementation:** `expires_in = settings.jwt_access_token_expire_minutes * 60`  
**Test:** Unit test: expires_in matches settings calculation

### PR125-SR-008: No Raw Token Leakage

**EDD Component:** Router, error handlers  
**Implementation:** Raw refresh tokens, access tokens, token hashes, and JWT payloads are never logged or returned in error responses.  
**Test:** Unit test: response does not echo input refresh token

### PR125-SR-009: Session Service Call Count

**EDD Component:** POST /auth/refresh  
**Implementation:** `SessionService.refresh_session()` called exactly once per request.  
**Test:** Unit test: mock assertion verifies single call

### PR125-SR-010: Transaction Integrity

**EDD Component:** get_db(), SessionService  
**Implementation:** Uses existing `get_db()` dependency. Single database session per request. Existing commit/rollback behavior preserved.  
**Test:** Existing PR-1.2.3 transaction tests apply; no new transaction logic added

### PR125-SR-011: Security Event Preservation

**EDD Component:** SessionService  
**Implementation:** All existing security events preserved:
- Success → TOKEN_REFRESHED
- Invalid token → TOKEN_REFRESH_FAILED
- Reuse/revoked → SESSION_REUSE_DETECTED + ALL_SESSIONS_REVOKED
- Timeout → SESSION_EXPIRED  
**Test:** Existing PR-1.2.3 event tests apply; Phase 2 tests verify no duplicate events

### PR125-SR-012: Public Route Registration

**EDD Component:** public_routes.py  
**Implementation:** All four auth endpoints added to PUBLIC_ROUTES set.  
**Test:** Unit test: all four endpoints appear in PUBLIC_ROUTES

---

## 10. Conclusion

This EDD defines the complete design for PR-1.2.5 Authentication APIs. It enables the full authentication lifecycle (refresh, token issuance, logout) while preserving all PR-1.2.3 and PR-1.2.4 security invariants. The revoked-session behavior aligns with the existing SessionService semantics: a refresh token for a revoked DeviceSession triggers the reuse-detection path (401 REFRESH_TOKEN_REUSED, SESSION_REUSE_DETECTED, ALL_SESSIONS_REVOKED), maintaining a single failure mode for token compromise scenarios.





