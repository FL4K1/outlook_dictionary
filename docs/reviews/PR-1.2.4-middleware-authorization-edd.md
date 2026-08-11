# PR-1.2.4 Middleware & Authorization — Engineering Design Document

**Status:** APPROVED
**Date:** 2026-08-08
**Branch:** feature/pr-1.2.4-middleware-authorization
**Base Release:** v0.3.0-alpha.2
**Scope:** PR-1.2.4 Middleware & Authorization
**Authoritative Security Baseline:** PR-1.2.4 Security Threat Model (approved)

---

## 1. Executive Summary

### Current Authentication Enforcement Gap

PR-1.2.1 through PR-1.2.3 established the platform's authentication infrastructure: JWT access tokens, opaque refresh tokens, stable DeviceSession identity, RefreshTokenFamily epoch rotation, and session lifecycle management. However, no HTTP-boundary enforcement exists. Today, the FastAPI application has no authentication middleware, no JWT validation in the request path, no AuthenticationContext hydration, and no authorization checks. Any unauthenticated client can reach any endpoint.

### Objective of PR-1.2.4

PR-1.2.4 closes this gap by introducing the first request-time authentication and authorization enforcement layer. It adds:

- AuthenticationMiddleware — validates JWT access tokens and DeviceSession state at the HTTP boundary
- AuthenticationContext hydration — server-generates an immutable, request-scoped identity/authorization object
- PolicyEngine — centralized, default-deny authorization decisions based on server-resolved roles and permissions
- Authorization dependencies — FastAPI Depends() primitives for route protection
- Public route handling — explicit allow-list with default-deny semantics

### Architectural Outcome

The request flow becomes:

HTTP Request
→ AuthenticationMiddleware
→ JWT verification + DeviceSession validation
→ AuthenticationContext hydration
→ Router (with authorization dependencies)
→ Service
→ Repository
→ Database

All identity and authorization state is server-resolved. Client-supplied claims are never trusted. The stable DeviceSession architecture from PR-1.2.3 is preserved intact.

### Security Outcome

PR-1.2.4 eliminates the current authentication bypass gap and enforces tenant isolation at the HTTP boundary. It addresses all CRITICAL and HIGH threats identified in the PR-1.2.4 Threat Model: authentication bypass, JWT attacks, session hijacking, privilege escalation, cross-tenant access, and context spoofing.

---

## 2. Scope

### In Scope

| Component | Description |
| :--- | :--- |
| AuthenticationMiddleware | FastAPI middleware that extracts, verifies, and validates JWT + DeviceSession |
| JWT Request Validation | Integration with existing TokenService/SigningProvider for cryptographic verification |
| DeviceSession Validation | Database lookup and state validation (revoked, expired, idle timeout) |
| Tenant/Organization Validation | Verify JWT tid/oid against DeviceSession's tenant/organization |
| AuthenticationContext Hydration | Server-side population per request from database records |
| Membership Resolution | Load user's active memberships for the tenant |
| Role/Permission Resolution | Resolve roles from memberships, permissions from roles |
| PolicyEngine | Centralized authorization with default-deny semantics |
| Authorization Dependencies | require_permission(), require_role(), require_tenant_membership() |
| Protected Route Enforcement | Apply authorization dependencies to route handlers |
| Public Route Handling | Explicit allow-list for unauthenticated endpoints |
| Security Events | Emit events for authentication/authorization decisions |
| Tests | Unit, integration, and security tests for all above |

### Out of Scope

| Component | Reason | Future Milestone |
| :--- | :--- | :--- |
| OAuth 2.0 / OIDC flows | Provider integration | PR-1.3 |
| Microsoft Entra ID | Provider integration | PR-1.3 |
| MFA / TOTP | Provider concern | PR-1.3+ |
| Rate limiting | Not required for initial enforcement | PR-1.2.5+ |
| Brute-force protection | Not required for initial enforcement | PR-1.2.5+ |
| Durable audit storage | Current: structlog | PR-1.2.5+ |
| Redis authorization caching | No caching in PR-1.2.4 | PR-1.2.5+ |
| Service-to-service auth | Not required for initial middleware | PR-1.2.5+ |
| Mail sync/search/AI security | Out of scope per EDD | PR-1.5+ |
| Frontend security | Next.js is separate | PR-1.9+ |
| Production hardening | Alpha stage | v1.0.0 |

---

## 3. Architecture

### Target Request Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENT (UNTRUSTED)                              │
│                                                                         │
│  Sends: Authorization: Bearer <jwt>                                     │
│  JWT claims: sub, tid, oid, sid, jti, iss, aud, exp, nbf              │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      FASTAPI MIDDLEWARE STACK                            │
│                                                                         │
│  Order (outermost → innermost):                                        │
│  1. SecurityHeadersMiddleware (existing)                               │
│  2. RequestLoggingMiddleware (existing)                                │
│  3. RequestIdMiddleware (existing)                                     │
│  4. CORSMiddleware (existing)                                          │
│  5. AuthenticationMiddleware (NEW — PR-1.2.4)                         │
│     MUST be outermost among business middleware                         │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   AUTHENTICATION MIDDLEWARE                              │
│                                                                         │
│  1. Extract JWT from Authorization header                               │
│  2. Verify JWT via TokenService.verify_access_token()                   │
│     - Signature, algorithm, issuer, audience, expiry, claims            │
│  3. Lookup DeviceSession by sid claim                                    │
│  4. Validate DeviceSession:                                             │
│     - revoked_at IS NULL                                                │
│     - expires_at > now (absolute timeout)                               │
│     - last_active_at within idle window                                 │
│  5. Validate tenant/organization consistency:                           │
│     - DeviceSession.tenant_id == JWT.tid                                │
│     - Tenant.organization_id == JWT.oid                                 │
│  6. Hydrate AuthenticationContext from database                         │
│  7. Store in request.state                                              │
│  8. Call next()                                                         │
│                                                                         │
│  On failure: raise UnauthorizedError (401)                              │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        ROUTER / ENDPOINT                                 │
│                                                                         │
│  - Public routes: no auth required                                      │
│  - Protected routes: Depends(get_current_context)                       │
│  - Additional authz: Depends(require_permission(...))                   │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      SERVICE LAYER                                       │
│                                                                         │
│  - Receives AuthenticationContext                                       │
│  - May perform additional authorization via PolicyEngine                 │
│  - Business logic only, no auth checks bypassed                         │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    REPOSITORY LAYER                                      │
│                                                                         │
│  - Tenant isolation enforced via tenant_id filters                      │
│  - All queries parameterized via SQLAlchemy ORM                         │
│  - Never trusts client-supplied tenant/user IDs                         │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATABASE                                         │
│                                                                         │
│  PostgreSQL — source of truth for sessions, memberships, roles,         │
│  permissions                                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Responsibility Matrix

| Layer | Responsibility | Must Not |
| :--- | :--- | :--- |
| AuthenticationMiddleware | JWT extraction, verification, DeviceSession validation, AuthenticationContext creation | Bypass TokenService, trust client input, create partial context |
| AuthenticationContext | Immutable request-scoped identity/authorization state | Be modified after creation, be cached globally, be derived from client input |
| PolicyEngine | Authorization decisions (allow/deny) | Trust client claims, default to allow, bypass membership checks |
| Authorization Dependencies | Route-level enforcement | Perform business logic, bypass PolicyEngine |
| Services | Business logic | Perform authorization (use PolicyEngine instead) |
| Repositories | Data access with tenant isolation | Bypass tenant filters, accept client-supplied IDs |

---

## 4. AuthenticationMiddleware Design

### Type

AuthenticationMiddleware inherits from starlette.middleware.base.BaseHTTPMiddleware.

### Request Lifecycle

```
dispatch(request, call_next)
  │
  ├─ 1. Extract JWT from Authorization header
  │     └─ Missing header → raise UnauthorizedError(401)
  │
  ├─ 2. Verify JWT via TokenService.verify_access_token()
  │     └─ Invalid/expired/forged → raise UnauthorizedError(401)
  │
  ├─ 3. Extract sid, tid, oid from verified JWT payload
  │
  ├─ 4. Lookup DeviceSession by sid
  │     └─ Not found → raise UnauthorizedError(401)
  │
  ├─ 5. Validate DeviceSession state
  │     ├─ revoked_at IS NOT NULL → raise UnauthorizedError(401) + emit SECURITY_EVENT
  │     ├─ expires_at < now → raise UnauthorizedError(401) + emit SECURITY_EVENT
  │     └─ last_active_at < (now - idle_window) → raise UnauthorizedError(401) + emit SECURITY_EVENT
  │
  ├─ 6. Validate tenant/organization consistency
  │     ├─ DeviceSession.tenant_id != JWT.tid → raise UnauthorizedError(401)
  │     └─ Tenant.organization_id != JWT.oid → raise UnauthorizedError(401)
  │
  ├─ 7. Hydrate AuthenticationContext
  │     ├─ Load Membership(s) for (user_id, tenant_id)
  │     ├─ Load Role(s) from Membership
  │     ├─ Load Permission(s) from Role
  │     └─ Create immutable AuthenticationContext
  │
  ├─ 8. Store context in request.state.auth_context
  │
  └─ 9. return await call_next(request)
```

### JWT Extraction

- Header: Authorization: Bearer <token>
- No other header sources are trusted
- Missing header → 401
- Malformed header → 401
- No cookie-based JWT extraction (prevents CSRF)

### TokenService Integration

AuthenticationMiddleware delegates ALL cryptographic operations to the existing TokenService:

| Operation | Method | Purpose |
| :--- | :--- | :--- |
| JWT verification | TokenService.verify_access_token(token) | Signature, algorithm, issuer, audience, expiry, claims |
| JWT decoding | Returns AccessTokenSubject | Provides user_id, tenant_id, organization_id, session_id |

AuthenticationMiddleware MUST NOT:

- Call jwt.decode() directly
- Implement its own algorithm validation
- Implement its own expiry checking
- Bypass SigningProvider

### Claim Validation

After TokenService.verify_access_token() returns AccessTokenSubject, middleware validates:

| Claim | Validation | Failure Behavior |
| :--- | :--- | :--- |
| sub | Must be valid UUID | 401 |
| sid | Must resolve to existing DeviceSession | 401 |
| tid | Must match DeviceSession.tenant_id | 401 |
| oid | Must match Tenant.organization_id | 401 |
| jti | Recorded for replay detection (future) | No failure — observability only |

### DeviceSession Lookup

- Query: SELECT * FROM device_sessions WHERE id = :sid
- Index: ix_device_sessions_current_refresh_token_hash is NOT used; lookup by primary key id
- Repository method: DeviceSessionRepository.get(id=sid)

### Session State Validation

| State | Check | Failure |
| :--- | :--- | :--- |
| Revoked | revoked_at IS NOT NULL | 401 + SESSION_REUSE_DETECTED event |
| Absolute timeout | expires_at < now() | 401 + SESSION_EXPIRED event |
| Idle timeout | last_active_at < (now() - idle_window) | 401 + SESSION_EXPIRED event |

Clock skew tolerance: 120 seconds (configurable via Settings.jwt_clock_skew_seconds).

### Tenant/Organization Validation

After DeviceSession lookup:

1. Verify DeviceSession.tenant_id == AccessTokenSubject.tenant_id
2. Load Tenant by DeviceSession.tenant_id
3. Verify Tenant.organization_id == AccessTokenSubject.organization_id

Failure at any step → 401. This prevents cross-tenant and cross-organization token reuse.

### AuthenticationContext Creation

See Section 5 for full specification.

### Error Handling

All authentication failures:

- Raise UnauthorizedError (HTTP 401)
- Emit SecurityEvent with SecurityOutcome.FAILURE
- Do NOT expose whether a user exists, whether a session exists, or which specific check failed
- Response body: standard ErrorResponse with error.code = "UNAUTHORIZED"

### Public Route Handling

See Section 9 for full specification.

### Middleware Ordering

AuthenticationMiddleware MUST be registered as the outermost business middleware in main.py. FastAPI applies middleware in reverse registration order, so it must be the last app.add_middleware() call among authentication-related middleware.

Current middleware order (innermost → outermost):

1. SecurityHeadersMiddleware
2. RequestLoggingMiddleware
3. RequestIdMiddleware
4. CORSMiddleware
5. AuthenticationMiddleware ← NEW, outermost

This ensures authentication completes before any route handler executes.

---

## 5. AuthenticationContext

### Resolution of AD-001

Decision: AuthenticationContext is created by AuthenticationMiddleware and stored in request.state.auth_context.

### Exact Fields

@dataclass(frozen=True, slots=True)
class AuthenticationContext:
    request_id: str                    # From RequestIdMiddleware
    correlation_id: str                # From request state or generated
    user_id: uuid.UUID                 # From verified JWT + DeviceSession
    tenant_id: uuid.UUID               # From verified JWT + DeviceSession
    organization_id: uuid.UUID         # From verified JWT + Tenant
    session_id: uuid.UUID              # From verified JWT + DeviceSession
    membership_id: uuid.UUID | None    # Active membership for tenant
    role_ids: frozenset[uuid.UUID]     # From active membership's role
    role_names: frozenset[str]         # Role names for require_role()
    permissions: frozenset[str]        # Resolved from roles
    authentication_method: str         # "session"
    provider: str | None               # None for platform auth
    authenticated_at: datetime | None  # Current UTC timestamp
    request_ip: str | None             # From request.client.host
    user_agent: str | None             # From request.headers
    is_service_account: bool           # False for human users

### Immutable Structure

- @dataclass(frozen=True, slots=True) — immutable after creation
- No setters, no property setters
- All fields set at construction time

### Creation Location

Created in AuthenticationMiddleware.dispatch() AFTER all validations pass:

1. JWT verified
2. DeviceSession validated
3. Tenant/organization verified
4. Membership loaded
5. Roles resolved
6. Permissions resolved

### Request.State Usage

request.state.auth_context = context

- request.state is request-scoped by Starlette/FastAPI
- No global state, no caching, no async contextvars for context itself
- request_id is already bound to structlog contextvars by RequestIdMiddleware

### Lifecycle

- Created: Per authenticated request, in middleware
- Read: Via Depends(get_current_context) in route handlers/services
- Destroyed: At end of request (garbage collected with request state)

### Retrieval Dependency

async def get_current_context(request: Request) -> AuthenticationContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise UnauthorizedError("Authentication required.")
    return context

### Protection Against Spoofing

| Threat | Mitigation |
| :--- | :--- |
| Client-supplied context | Middleware creates context server-side from DB; client input ignored |
| Context reuse across requests | request.state is per-request; no global storage |
| Async context leakage | No async contextvars for context; request.state is isolated |
| Frozen dataclass tampering | frozen=True prevents modification after creation |

### Concurrent-Request Isolation

- Each HTTP request has its own request.state
- ASGI guarantees request isolation
- No shared mutable state between concurrent requests

---

## 6. Membership / Role / Permission Architecture

### Resolution of AD-005

Decision: Eager resolution per request. No caching in PR-1.2.4. Future caching must not be precluded.

### Repository Interfaces

#### MembershipRepository

class MembershipRepository(BaseRepository[Membership]):
    def __init__(self, session: AsyncSession) -> None: ...

    async def get_active_membership(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Membership | None:
        """Get active membership for (user_id, tenant_id).

        Returns None if no active membership exists.
        """

Query:

SELECT * FROM memberships
WHERE user_id = :user_id
  AND tenant_id = :tenant_id
  AND is_active = true
LIMIT 1

#### RoleRepository

class RoleRepository(BaseRepository[Role]):
    def __init__(self, session: AsyncSession) -> None: ...

    async def get_by_name(self, name: str, tenant_id: uuid.UUID | None = None) -> Role | None:
        """Get role by name, optionally filtered by tenant.

        System roles have tenant_id = NULL.
        Tenant roles have tenant_id = specific tenant.
        """

#### PermissionRepository

class PermissionRepository(BaseRepository[Permission]):
    def __init__(self, session: AsyncSession) -> None: ...

    async def get_by_codename(self, codename: str) -> Permission | None:
        """Get permission by unique codename."""

    async def get_permissions_for_role(self, role_id: uuid.UUID) -> list[Permission]:
        """Get all permissions assigned to a role via role_permissions."""

### Resolution Sequence

DeviceSession
  └─ user_id, tenant_id
      │
      ▼
MembershipRepository.get_active_membership(user_id, tenant_id)
  └─ Returns Membership with role_id
      │
      ▼
RoleRepository.get(role_id)
  └─ Returns Role with name, is_system, tenant_id
      │
      ▼
PermissionRepository.get_permissions_for_role(role_id)
  └─ Returns list of Permission with codename
      │
      ▼
AuthenticationContext(
  membership_id = membership.id,
  role_ids = frozenset({role.id}),
  role_names = frozenset({role.name}),
  permissions = frozenset({p.codename for p in permissions})
)

### Tenant Filtering

- MembershipRepository.get_active_membership() filters by user_id AND tenant_id
- RoleRepository respects tenant_id for tenant-scoped roles
- PermissionRepository does not filter by tenant (permissions are global)

### Active Membership Semantics

- Membership.is_active = true required
- is_active = false → membership revoked → authorization denied
- Soft delete via is_active flag, not hard delete

### Role Resolution

- One membership → one role
- No role hierarchy in PR-1.2.4 (single role per membership)
- System roles (is_system = true, tenant_id = NULL) are available to all tenants
- Tenant roles (tenant_id = specific tenant) are tenant-scoped

### Permission Resolution

- Permissions are global (not tenant-scoped)
- Permission codenames use dot notation: tenant.read, user.invite, mail_account.connect
- Permissions assigned to roles via role_permissions join table

### Freshness Requirements

- Permissions resolved from database at authentication time
- No caching in PR-1.2.4
- JWT TTL is 15 minutes — maximum staleness is 15 minutes
- Future caching must have TTL ≤ JWT TTL

---

## 7. PolicyEngine

### Resolution of AD-002

Decision: Synchronous interface. Accepts AuthenticationContext and returns authorization decision.

### Interface

class PolicyEngine:
    def __init__(
        self,
        membership_repo: MembershipRepository,
        role_repo: RoleRepository,
        permission_repo: PermissionRepository,
    ) -> None:
        ...

    def authorize(
        self,
        context: AuthenticationContext,
        resource: str,
        action: str,
        resource_owner_id: uuid.UUID | None = None,
    ) -> AuthorizationDecision:
        """Make an authorization decision.

        Args:
            context: The authenticated request context.
            resource: The resource type (e.g., "mail_account", "user").
            action: The action (e.g., "read", "write", "delete").
            resource_owner_id: Optional owner of the resource for ownership checks.

        Returns:
            AuthorizationDecision with allowed, reason, and missing permissions.
        """

### AuthorizationDecision

@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str | None = None
    missing_permissions: frozenset[str] = frozenset()

### Inputs

| Input | Source | Trusted |
| :--- | :--- | :--- |
| context | Middleware-hydrated AuthenticationContext | Yes — server-generated |
| resource | Route handler / service | Yes — code-defined constant |
| action | Route handler / service | Yes — code-defined constant |
| resource_owner_id | Route handler / service | Yes — from database |

### Outputs

| Output | Type | Description |
| :--- | :--- | :--- |
| allowed | bool | True if access granted |
| reason | str | None | Human-readable reason |
| missing_permissions | frozenset[str] | Permissions that would have allowed access |

### Default-Deny Semantics

PolicyEngine defaults to DENY. An explicit allow rule is required for every permitted action.

Algorithm:

1. If context.is_service_account is True → skip membership check, use service account scopes (future)
2. If context.permissions is empty → DENY
3. If resource.action is in context.permissions → ALLOW
4. If user is platform admin (User.is_platform_admin = true) → ALLOW (future explicit check)
5. Otherwise → DENY

### Conflict Resolution (SR-039)

- Single role per membership — no role hierarchy in PR-1.2.4
- Permission set is union — if any granted permission matches, allow
- Deny-wins is not applicable — no deny rules in RBAC; absence of permission is denial
- Deterministic: Permission check is set membership test — always deterministic

### Tenant Enforcement

- PolicyEngine receives AuthenticationContext which includes tenant_id
- All authorization decisions are implicitly tenant-scoped because AuthenticationContext is tenant-scoped
- Cross-tenant access requires a different AuthenticationContext, which is impossible without a different JWT + DeviceSession

### Resource Ownership

- resource_owner_id parameter enables ownership checks
- Example: policy.authorize(context, "mail_account", "read", resource_owner_id=mail_account.user_id)
- PolicyEngine checks: context.user_id == resource_owner_id OR required permission granted
- This is a future enhancement; PR-1.2.4 focuses on permission-based access

### Error Semantics

- PolicyEngine.authorize() returns AuthorizationDecision, never raises
- Authorization dependencies interpret the decision and raise InsufficientPermissionsError (HTTP 403) if denied
- PolicyEngine failure (e.g., DB error) → exception propagates → middleware catches → 500 or 403 (fail-closed)

---

## 8. Authorization Dependencies

### Resolution of AD-003

Decision: FastAPI dependency factory functions that wrap PolicyEngine.

### Patterns

#### require_permission

def require_permission(*required_permissions: str) -> Callable:
    """FastAPI dependency that requires one of the specified permissions.

    Usage:
        @router.get("/mail/accounts")
        async def list_accounts(
            context: AuthenticationContext = Depends(get_current_context),
            _: None = Depends(require_permission("mail_account.read")),
        ):
            ...
    """

Behavior:

- Extracts AuthenticationContext from request state
- Calls PolicyEngine.authorize(context, resource, action) for each permission
- If any permission granted → allow
- If none granted → raise InsufficientPermissionsError (HTTP 403)

#### require_role

def require_role(*required_roles: str) -> Callable:
    """FastAPI dependency that requires one of the specified roles.

    Usage:
        @router.post("/admin/users")
        async def create_user(
            context: AuthenticationContext = Depends(get_current_context),
            _: None = Depends(require_role("admin", "tenant_admin")),
        ):
            ...
    """

Behavior:

- Checks if any of context.role_names match the required role names
- If any match → allow
- If no match → raise InsufficientPermissionsError (HTTP 403)

#### require_tenant_membership

def require_tenant_membership() -> Callable:
    """FastAPI dependency that verifies active tenant membership.

    This is implicitly enforced by AuthenticationContext hydration,
    but this dependency provides explicit route-level enforcement
    and fails closed if context is missing.
    """

Behavior:

- Verifies context.membership_id is not None
- Verifies context.is_service_account is False for human users
- If no active membership → raise UnauthorizedError (HTTP 401)

### Composition

Dependencies can be composed:

@router.delete("/mail/accounts/{account_id}")
async def delete_account(
    context: AuthenticationContext = Depends(get_current_context),
    __: None = Depends(require_permission("mail_account.delete")),
    ___: None = Depends(require_tenant_membership()),
):
    ...

### PolicyEngine Interaction

Authorization dependencies delegate to PolicyEngine.authorize(). They do NOT:

- Query databases directly
- Implement permission logic
- Cache authorization decisions

### HTTP 403 Behavior

- InsufficientPermissionsError → HTTP 403
- Response body: {"error": {"code": "FORBIDDEN", "message": "You do not have permission to perform this action."}}
- No information about which specific permission is missing (prevents enumeration)
- Security event emitted: AUTHORIZATION_FAILURE

---

## 9. Public Route Architecture

### Resolution of AD-004

Decision: Explicit allow-list via FastAPI dependency exclusion. Default-deny.

### Mechanism

Public routes are declared by omitting authentication dependencies:

@router.get("/health/live")
async def liveness() -> HealthStatus:
    return HealthStatus(status="healthy")

@router.post("/auth/login")
async def login(request: Request, ...) -> AuthResponse:
    # No authentication required — this is the login endpoint
    ...

Protected routes require explicit dependencies:

@router.get("/api/v1/mail/accounts")
async def list_accounts(
    context: AuthenticationContext = Depends(get_current_context),
    _: None = Depends(require_permission("mail_account.read")),
) -> list[MailAccount]:
    ...

### Default-Deny Rule

- All routes are protected by default
- Developers must explicitly add Depends(get_current_context) to protected routes
- Routes without authentication dependencies are implicitly public
- CRITICAL: This is a developer discipline requirement. Future: automated route scanning to enforce.

### Public Route Categories

| Category | Examples | Notes |
| :--- | :--- | :--- |
| Health checks | /health/live, /health/ready, /health/startup | Already exist, must remain public |
| Authentication endpoints | /auth/login, /auth/refresh, /auth/logout | Public by definition |
| Documentation | /docs, /redoc | Disabled in production via settings.is_production |
| Static assets | /static/* | If applicable |

### Security Implications

- Forgetting to add auth dependencies = public endpoint
- Code review must verify all new routes have appropriate dependencies
- Future: CI linting to detect unprotected routes

---

## 10. Error Contract

### Resolution of AD-006

### HTTP Status Codes

| Scenario | Status Code | Error Code | Message |
| :--- | :--- | :--- | :--- |
| Missing JWT | 401 | UNAUTHORIZED | "Authentication is required." |
| Invalid JWT signature | 401 | UNAUTHORIZED | "Authentication is required." |
| Expired JWT | 401 | UNAUTHORIZED | "Authentication is required." |
| Revoked DeviceSession | 401 | UNAUTHORIZED | "Authentication is required." |
| Expired DeviceSession | 401 | UNAUTHORIZED | "Authentication is required." |
| Cross-tenant token | 401 | UNAUTHORIZED | "Authentication is required." |
| Insufficient permissions | 403 | FORBIDDEN | "You do not have permission to perform this action." |
| Missing tenant membership | 401 | UNAUTHORIZED | "Authentication is required." |

### Information Disclosure Policy

- 401 responses MUST NOT reveal:
  - Whether a user exists
  - Whether a session exists
  - Which specific check failed (signature vs expiry vs revocation)
  - The reason for failure beyond generic message
- 403 responses MUST NOT reveal:
  - Which specific permission is missing
  - The user's actual permissions
  - Resource existence
- Security events contain full details — but are logged server-side only, never returned to client

### Response Structure

All error responses use the existing ErrorResponse schema:

{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Authentication is required.",
    "request_id": "abc-123"
  }
}

### Exception Handling

- UnauthorizedError → 401
- ForbiddenError → 403
- InsufficientPermissionsError → 403
- All other exceptions → 500 (via existing unhandled_error_handler)
- Middleware catches all exceptions and converts to appropriate HTTP response

---

## 11. Security Events

### Required Events (from Threat Model)

| Event Type | Trigger | Required Fields | Prohibited Fields |
| :--- | :--- | :--- | :--- |
| AUTHENTICATION_SUCCESS | JWT valid, session valid, context hydrated | user_id, tenant_id, session_id, ip_address, user_agent, request_id | token, refresh_token, jti |
| AUTHENTICATION_FAILURE | JWT invalid, session invalid, or missing | reason, ip_address, user_agent, request_id | token, user_id (if not known) |
| AUTHORIZATION_SUCCESS | PolicyEngine allows | user_id, tenant_id, session_id, resource, action, permission | token, session_details |
| AUTHORIZATION_FAILURE | PolicyEngine denies | user_id, tenant_id, session_id, resource, action, missing_permission | token, session_details |
| SESSION_REUSE_DETECTED | Refresh token reuse (carried from PR-1.2.3) | session_id, reason | token, user_id |
| SESSION_EXPIRED | Absolute/idle timeout exceeded | session_id, reason | token, user_id |
| ALL_SESSIONS_REVOKED | Bulk revocation triggered | user_id, tenant_id, reason | token, session_details |

### Correlation

- All events include request_id from RequestIdMiddleware
- All events include UTC timestamp
- All events include SecurityOutcome.SUCCESS or SecurityOutcome.FAILURE

### Emission Points

| Event | Emitted By |
| :--- | :--- |
| AUTHENTICATION_SUCCESS | AuthenticationMiddleware |
| AUTHENTICATION_FAILURE | AuthenticationMiddleware |
| AUTHORIZATION_SUCCESS | Authorization dependencies |
| AUTHORIZATION_FAILURE | Authorization dependencies |
| SESSION_REUSE_DETECTED | SessionService (existing) |
| SESSION_EXPIRED | SessionService (existing) |
| ALL_SESSIONS_REVOKED | SessionService (existing) |

---

## 12. Middleware Ordering

### Invariant

AuthenticationMiddleware MUST execute before any route handler. It MUST be the outermost business middleware.

### FastAPI Middleware Ordering

FastAPI applies middleware in reverse registration order. If middleware is registered as:

app.add_middleware(A)
app.add_middleware(B)
app.add_middleware(C)

The actual execution order is: C → B → A → route handler

### Required Order

app.add_middleware(SecurityHeadersMiddleware)      # Innermost
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(CORSMiddleware)
app.add_middleware(AuthenticationMiddleware)       # Outermost

Execution order: AuthenticationMiddleware → CORSMiddleware → RequestIdMiddleware → RequestLoggingMiddleware → SecurityHeadersMiddleware → route handler

### Testable Invariant

Integration test MUST verify that a request to a protected endpoint WITHOUT a JWT receives 401 BEFORE any route handler logic executes. This can be verified by:

1. Adding a route that raises a unique exception
2. Sending unauthenticated request
3. Verifying 401 response, not the unique exception

---

## 13. Data Flow / Trust Boundaries

### Boundary 1: Client → HTTP

| What Crosses | Trust Level | Validation |
| :--- | :--- | :--- |
| JWT (Authorization header) | Untrusted | Cryptographic verification via TokenService |
| HTTP method, path, query params | Untrusted | FastAPI validation |
| Request body | Untrusted | Pydantic validation |
| Headers (X-Request-ID, etc.) | Partially trusted | RequestIdMiddleware validates/generates |

### Boundary 2: HTTP → AuthenticationMiddleware

| What Crosses | Trust Level | Validation |
| :--- | :--- | :--- |
| JWT token string | Untrusted | Must verify signature, algorithm, claims |
| Request metadata | Untrusted | IP, user-agent logged but not trusted |

### Boundary 3: AuthenticationMiddleware → AuthenticationContext

| What Crosses | Trust Level | Validation |
| :--- | :--- | :--- |
| user_id, tenant_id, org_id, session_id | Trusted | Derived from verified JWT + DB lookup |
| role_ids, permissions | Trusted | Derived from DB queries |
| request_id | Trusted | Generated by RequestIdMiddleware |

### Boundary 4: AuthenticationContext → PolicyEngine

| What Crosses | Trust Level | Validation |
| :--- | :--- | :--- |
| AuthenticationContext fields | Trusted | Immutable, server-generated |
| resource, action parameters | Trusted | Code-defined constants |

### Boundary 5: PolicyEngine → Repositories

| What Crosses | Trust Level | Validation |
| :--- | :--- | :--- |
| user_id, tenant_id, role_id, permission_id | Trusted | Derived from AuthenticationContext |
| Query parameters | Trusted | Code-defined |

### Boundary 6: Repositories → Database

| What Crosses | Trust Level | Validation |
| :--- | :--- | :--- |
| SQL queries | Trusted | Parameterized via SQLAlchemy ORM |
| tenant_id filters | Trusted | Enforced by repository code |

### Trust Boundary Summary

CLIENT (UNTRUSTED)
    ↓ [JWT, headers, body]
HTTP BOUNDARY
    ↓ [verify JWT, lookup session]
AUTHENTICATION MIDDLEWARE (TRUSTS DB, NOT CLIENT)
    ↓ [server-generated context]
AUTHENTICATION CONTEXT (IMMUTABLE, TRUSTED)
    ↓ [authorization decisions]
POLICY ENGINE (TRUSTS CONTEXT, NOT CLIENT)
    ↓ [parameterized queries]
REPOSITORIES (TRUSTS ORM)
    ↓ [SQL]
DATABASE (TRUSTED)

---

## 14. Security Requirements Traceability

### SR-025: Authentication Enforcement

EDD Component: AuthenticationMiddleware
Implementation: Middleware rejects unauthenticated requests with 401
Test: Integration test: unauthenticated request to protected route returns 401

### SR-026: JWT Signature Verification

EDD Component: AuthenticationMiddleware → TokenService.verify_access_token()
Implementation: All JWT validation delegated to TokenService
Test: Unit test: forged signature rejected

### SR-027: Algorithm Allow-list

EDD Component: TokenService (existing)
Implementation: HS256 only; alg:none rejected
Test: Unit test: alg:none token rejected (existing)

### SR-028: Token Expiry Validation

EDD Component: TokenService (existing)
Implementation: exp claim validated during JWT verification
Test: Unit test: expired token rejected (existing)

### SR-029: DeviceSession Revocation Check

EDD Component: AuthenticationMiddleware, DeviceSession validation step
Implementation: Query DeviceSession, check revoked_at IS NULL
Test: Integration test: valid JWT for revoked session returns 401

### SR-030: DeviceSession Timeout Check

EDD Component: AuthenticationMiddleware, DeviceSession validation step
Implementation: Check expires_at > now and last_active_at within idle window
Test: Integration test: expired/idle session returns 401

### SR-031: No Client-Supplied Identity

EDD Component: AuthenticationMiddleware, AuthenticationContext
Implementation: user_id/tenant_id/org_id derived from JWT + DB only
Test: Security test: modified JWT claims rejected

### SR-032: Session ID Validation

EDD Component: AuthenticationMiddleware, DeviceSession lookup
Implementation: JWT sid must resolve to existing DeviceSession
Test: Security test: substituted sid rejected

### SR-033: AuthenticationContext Integrity

EDD Component: AuthenticationContext, middleware
Implementation: Server-generated per request, frozen dataclass, request.state storage
Test: Unit test: context immutability; integration test: no cross-request leakage

### SR-034: Cross-Tenant Session Prevention

EDD Component: AuthenticationMiddleware, tenant validation step
Implementation: DeviceSession.tenant_id == JWT.tid
Test: Integration test: cross-tenant JWT rejected

### SR-035: Authorization Enforcement

EDD Component: PolicyEngine, authorization dependencies
Implementation: All protected routes use Depends(require_permission)
Test: Integration test: unauthorized access returns 403

### SR-036: Resource Ownership Verification

EDD Component: PolicyEngine
Implementation: resource_owner_id parameter in authorize()
Test: Unit test: ownership check enforced

### SR-037: Privilege Escalation Prevention

EDD Component: PolicyEngine, authorization dependencies
Implementation: Default-deny, explicit permission checks
Test: Security test: regular user cannot access admin endpoints

### SR-038: Membership Validity Check

EDD Component: AuthenticationMiddleware, MembershipRepository
Implementation: Load active membership; is_active = true required
Test: Integration test: revoked membership returns 401/403

### SR-039: Deterministic Conflict Resolution

EDD Component: PolicyEngine
Implementation: Single role per membership; permission set union; deny by absence
Test: Unit test: deterministic allow/deny for overlapping permissions

### SR-040: Tenant-Scoped Authorization

EDD Component: AuthenticationContext, PolicyEngine
Implementation: Context includes tenant_id; all authz decisions tenant-scoped
Test: Integration test: cross-tenant resource access denied

### SR-041: Middleware Ordering

EDD Component: main.py, middleware registration
Implementation: AuthenticationMiddleware registered outermost
Test: Integration test: middleware ordering verified

### SR-042: Public Route Exclusion

EDD Component: Route definitions, get_current_context dependency
Implementation: Routes without auth dependencies are public; default-deny philosophy
Test: Integration test: public routes accessible without JWT

### SR-043: AuthenticationContext Isolation

EDD Component: request.state, middleware lifecycle
Implementation: Fresh context per request, no global state
Test: Integration test: concurrent requests receive independent contexts

### SR-044: Fail-Closed Middleware

EDD Component: AuthenticationMiddleware error handling
Implementation: All exceptions caught, return 401/403
Test: Unit test: middleware exception returns 401

### SR-045: PolicyEngine Default Deny

EDD Component: PolicyEngine.authorize()
Implementation: Returns allowed=False when no permission matches
Test: Unit test: unknown resource/action denied

### SR-046: Explicit Permission Denial

EDD Component: Authorization dependencies
Implementation: require_permission raises InsufficientPermissionsError (403)
Test: Unit test: missing permission returns 403

### SR-047: No PolicyEngine Bypass

EDD Component: Route definitions, authorization dependencies
Implementation: All protected routes MUST use Depends(require_permission)
Test: Code review + future automated route scanning

### SR-048: Tenant ID Verification

EDD Component: AuthenticationMiddleware
Implementation: DeviceSession.tenant_id == JWT.tid
Test: Security test: modified tid rejected

### SR-049: Organization ID Verification

EDD Component: AuthenticationMiddleware
Implementation: Tenant.organization_id == JWT.oid
Test: Security test: modified oid rejected

### SR-050: Membership Validation

EDD Component: AuthenticationMiddleware, PolicyEngine
Implementation: Active membership required; is_active = true
Test: Integration test: revoked membership denied

### SR-051: Role Resolution Freshness

EDD Component: Membership/Role/Permission repositories
Implementation: Resolved from DB at authentication time; no caching
Test: Integration test: role change takes effect on next JWT expiry

### SR-052: Authorization Event Emission

EDD Component: Authorization dependencies, PolicyEngine
Implementation: Emit SecurityEvent for all allow/deny decisions
Test: Unit test: events emitted with correct fields

---

## 15. Conclusion

This EDD defines the complete design for PR-1.2.4 Middleware & Authorization. It closes the authentication enforcement gap while preserving the stable DeviceSession architecture from PR-1.2.3. All security requirements SR-025 through SR-060 are addressed through a combination of middleware enforcement, server-side authorization, and security event emission.
