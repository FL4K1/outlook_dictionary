# PR-1.3 Provider Integration — Engineering Design Document

**Status:** DRAFT — REQUIRES APPROVAL  
**Date:** 2026-08-15  
**Branch:** feature/pr-1.3-provider-integration (not yet created)  
**Base Release:** v0.3.0-alpha.4  
**Scope:** PR-1.3 Provider Integration — Microsoft Entra ID  
**Authoritative Security Baseline:** PR-1.3 Security Threat Model (not yet created)  
**Depends On:** Authentication APIs (v0.3.0-alpha.4)

---

## 1. Executive Summary

### Current State

PR-1.2.5 closed the platform authentication API gap by introducing `POST /auth/token`, `POST /auth/refresh`, `POST /auth/logout`, and `POST /auth/logout-all`. The platform now has a complete internal authentication lifecycle: platform JWTs, opaque refresh tokens, stable `DeviceSession` identity, `RefreshTokenFamily` epoch rotation, and HTTP-boundary enforcement via `AuthenticationMiddleware`.

However, the platform has no external identity provider integration. Users cannot authenticate via Microsoft Entra ID. No OAuth 2.0 / OIDC flow exists for platform identity. No identity linking mechanism exists. No provider credential storage exists for identity provider tokens.

### Objective of PR-1.3

PR-1.3 introduces Microsoft Entra ID as an external identity provider. Users may authenticate to the platform using their Entra ID identity. The platform links the Entra identity to an internal `User` record, issues platform JWTs for subsequent API access, and stores provider credentials securely for background operations (mail synchronization).

### Architectural Outcome

```
EXTERNAL IDP (Microsoft Entra ID)
    │
    ▼
Authorization Code Flow + PKCE
    │
    ▼
Provider Adapter validates ID token, resolves user
    │
    ▼
AuthenticationService.create_session_tokens()
    │
    ▼
Platform DeviceSession + JWT (same as platform auth)
    │
    ▼
Provider Credential Storage (separate from platform tokens)
    │
    ▼
Background workers use stored provider credentials for mail sync
```

### Security Outcome

PR-1.3 preserves all PR-1.2.x security invariants while adding:
- External identity binding via the existing `Identity` model
- Provider credential encryption at rest
- OIDC token validation with JWKS
- Identity linking that prevents cross-tenant identity reuse
- Security events for all provider authentication outcomes
- Request ID propagation into provider security events

---

## 2. Release / Milestone Version

### CONFIRMED EXISTING REPOSITORY FACT

The repository contains a version naming inconsistency:

- `docs/ai/CURRENT_SPRINT.md:30` — references **v0.3.0-alpha.5** for Provider Integration (PR-1.3)
- `docs/ai/ROADMAP.md:41` — references **v0.4.0-alpha.1** for Microsoft Entra ID Provider Integration

These documents were not modified by this investigation.

### OPEN DESIGN DECISION — REQUIRES APPROVAL

**Decision:** What is the authoritative release version for PR-1.3?

**Options:**
1. **v0.3.0-alpha.5** — aligns with `CURRENT_SPRINT.md`; treats PR-1.3 as a continuation of the v0.3.0 alpha stream within the PR-1.2 Authentication milestone.
2. **v0.4.0-alpha.1** — aligns with `ROADMAP.md`; treats PR-1.3 as the start of a new milestone stream after PR-1.2.

**Implications:**
- Option 1 implies PR-1.3 is still within the "PR-1.2 Authentication" milestone.
- Option 2 implies PR-1.3 closes the "PR-1.2 Authentication" milestone and opens a new milestone.
- The choice affects documentation, tagging, and downstream milestone planning.

**Classification:** LOW IMPACT.

Release target remains unresolved between v0.3.0-alpha.5 and v0.4.0-alpha.1 and must be resolved before release.

No source files are modified by this EDD pending this decision.

---

## 3. Scope

### In Scope

| Component | Description |
| :--- | :--- |
| Microsoft Entra ID OAuth 2.0 / OIDC | Authorization Code flow with PKCE for platform identity authentication |
| Identity Provider Abstraction | Separate from `MailAuthProvider`; covers Entra ID authentication, token validation, and user resolution |
| Identity Linking | Link Entra ID identities to existing platform `User` records via the existing `Identity` model |
| Provider Credential Storage | Encrypted storage of Entra ID access/refresh tokens for background worker use |
| Security Events | Emit existing `SecurityEventType` values for provider authentication outcomes |
| Callback Endpoint | `POST /auth/callback/entra` — OAuth2 callback for Entra ID authorization code exchange |
| Authorization Initiation | `GET /auth/entra` — Initiates Entra ID authorization flow |
| JIT Provisioning | Automatic `User` creation for new Entra ID identities with tenant assignment |
| Token Validation | Provider-specific ID token and access token validation separate from platform JWT path |

### Out of Scope

| Component | Reason | Future Milestone |
| :--- | :--- | :--- |
| MFA / TOTP | Not in PR-1.3 scope | PR-1.3+ |
| Frontend authentication UI | Next.js is separate | PR-1.9+ |
| Service-to-service authentication | Not required for initial provider integration | PR-1.3+ |
| Rate limiting / brute-force protection | Not required for initial implementation | PR-1.3+ |
| Mail sync/search/AI security | Out of scope per EDD | PR-1.5+ |
| Production hardening | Alpha stage | v1.0.0 |
| Other identity providers (Google, Okta, SAML) | PR-1.3 is Microsoft Entra ID only | PR-1.3+ |
| Platform JWT claim expansion | JWT claims are frozen | Never without approved ADR |
| Password authentication | Platform auth remains provider-first | Never |
| Platform session isolation change | Provider auth is isolated from platform auth by design | Never |

---

## 4. Current Architecture

### 4.1 Existing Authentication Stack

The platform authentication stack is established and stable:

| Layer | Component | Status |
| :--- | :--- | :--- |
| Token Issuance | `TokenService` + `SigningProvider` (HS256) | Implemented (PR-1.2.2) |
| Session Management | `SessionService` + `DeviceSession` + `RefreshTokenFamily` | Implemented (PR-1.2.3) |
| HTTP Enforcement | `AuthenticationMiddleware` + `AuthenticationContext` | Implemented (PR-1.2.4) |
| API Surface | `POST /auth/token`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/logout-all` | Implemented (PR-1.2.5) |
| Authorization | `PolicyEngine` + `require_permission()` / `require_role()` / `require_tenant_membership()` | Implemented (PR-1.2.4) |
| Security Events | `SecurityEventEmitter` + `SecurityEventType` enum | Implemented (PR-1.2.1) |
| Public Routes | `PUBLIC_ROUTES` allow-list | Implemented (PR-1.2.4) |

### 4.2 Existing Provider Abstractions

The repository has mail provider abstractions that are **not** directly reusable for identity provider integration:

| Component | Purpose | Reusable for PR-1.3? |
| :--- | :--- | :--- |
| `MailAuthProvider` Protocol | Mail OAuth (Graph API access) | No — different OAuth purpose |
| `MailSyncProvider` Protocol | Mail synchronization | No — unrelated |
| `MailWebhookProvider` Protocol | Mail webhooks | No — unrelated |
| `ProviderRegistry` | Resolves mail providers by type | No — registry is mail-specific |

### 4.3 Existing Identity-Related Models

| Model | Purpose | Status |
| :--- | :--- | :--- |
| `Identity` | External identity binding (provider + provider_user_id) | Implemented, migrated |
| `User` | Platform user | Implemented, migrated |
| `Membership` | User-Tenant join with Role | Implemented, migrated |
| `ProviderCredential` | Encrypted OAuth tokens for mail accounts | Implemented, scoped to mail |

---

## 5. Existing Repository Contracts

### 5.1 Provider Authentication Isolation

**Source:** `docs/ai/DECISION_LOG.md:7-12`

> "Provider authentication is strictly isolated from platform authentication. Mail intelligence relies heavily on background synchronization. If provider credentials (e.g., Microsoft Graph tokens) expire or are revoked, the user's platform session must remain valid so they can be prompted to re-authorize, rather than being abruptly logged out."

**Contract:** Platform `DeviceSession` and provider credentials have independent lifecycles. Provider credential failure must never revoke a platform session.

### 5.2 JWT Claim Freeze

**Source:** `apps/api/app/auth/tokens.py:30-36`

```python
ALLOWED_JWT_ALGORITHMS = frozenset({"HS256"})
REQUIRED_ACCESS_TOKEN_CLAIMS = frozenset(
    {"iss", "aud", "sub", "iat", "nbf", "exp", "jti", "sid", "tid", "oid"}
)
FORBIDDEN_ACCESS_TOKEN_CLAIMS = frozenset(
    {"role", "roles", "permissions", "mailbox_ids", "provider_token", "provider_refresh_token"}
)
```

**Contract:** Platform JWTs never contain provider tokens, roles, permissions, or authorization data. This contract is frozen.

### 5.3 AuthenticationService Boundary

**Source:** `apps/api/app/auth/service.py:42-49`

`AuthenticationService` is provider-agnostic. It creates platform sessions and tokens given `user_id`, `tenant_id`, and `organization_id`. Provider adapters are responsible for resolving these IDs before calling into `AuthenticationService`.

### 5.4 Immutable AuthenticationContext

**Source:** `apps/api/app/auth/context.py:18-42`, `apps/api/app/auth/context.py:56-65`

`AuthenticationContext` is a frozen dataclass. It is created server-side per request and stored in `request.state`. The `provider: str | None` field exists but is currently always `None`.

### 5.5 Default-Deny Authorization

**Source:** `apps/api/app/auth/policy.py:31-101`

`PolicyEngine` defaults to DENY. All protected routes must explicitly declare authorization dependencies. Public routes are explicit via `PUBLIC_ROUTES`.

### 5.6 Tenant Isolation

**Source:** `apps/api/app/auth/middleware.py:254-299`

Middleware enforces:
- `DeviceSession.tenant_id == JWT.tid`
- `Tenant.organization_id == JWT.oid`

Cross-tenant token reuse is rejected.

### 5.7 Security Event Contract

**Source:** `apps/api/app/auth/events.py:95-193`

All security events are immutable `SecurityEvent` dataclasses emitted via `security_event_emitter`. Events include `request_id` and structured metadata. No raw tokens or secrets are ever included in events.

---

## 6. Identity Provider Architecture

### 6.1 CONFIRMED EXISTING REPOSITORY FACT

The `MailAuthProvider` Protocol exists at `packages/providers/src/mip_providers/base.py:111-129` and is exclusively for mail-provider OAuth flows. It is documented as:

> "Interface for authenticating with a mail provider."

The `ProviderRegistry` at `packages/providers/src/mip_providers/registry.py` registers mail providers by type string (e.g., `"microsoft_graph"`).

### 6.2 CONFIRMED EXISTING REPOSITORY FACT

The `Identity` model exists at `packages/models/src/mip_models/user.py:97-153` with:
- `provider: Mapped[str]` — supports `"microsoft"`
- `provider_user_id: Mapped[str]` — Entra ID's `sub` claim
- `provider_email: Mapped[str | None]`
- `provider_metadata: Mapped[dict | None]` — JSONB
- Unique constraint on `(provider, provider_user_id)`

The `identities` table was created in `apps/api/alembic/versions/0001_initial_schema.py:110-138`.

### 6.3 PR-1.3 DESIGN DECISION

A new identity provider abstraction is required. The existing `MailAuthProvider` Protocol MUST NOT be repurposed for platform identity authentication.

**Proposed abstraction:**

```python
# Location: TBD (see AD-PR13-001)
@runtime_checkable
class IdentityProviderAuth(Protocol):
    """Interface for authenticating with an external identity provider."""
    
    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Generate the OAuth authorization URL with PKCE."""
        ...
    
    async def validate_callback(
        self,
        code: str,
        state: str,
        expected_state: str,
        expected_nonce: str,
        code_verifier: str,
    ) -> IdentityVerificationResult:
        """Validate callback, exchange code, verify ID token."""
        ...
    
    async def refresh_credentials(
        self,
        identity: Identity,
    ) -> ProviderCredentialSet:
        """Refresh expired provider tokens."""
        ...


@dataclass(frozen=True)
class IdentityVerificationResult:
    """Result of Entra ID identity verification."""
    provider_user_id: str
    provider_email: str | None
    provider_metadata: dict[str, object]
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderCredentialSet:
    """OAuth tokens returned by the identity provider."""
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: list[str] = field(default_factory=list)
```

### 6.3 AD-PR13-001: Identity Provider Abstraction

**Decision:**
Identity authentication providers use a dedicated abstraction separate from mail-provider authentication.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

A new `IdentityProviderAuth` Protocol must be created in `packages/providers/src/mip_providers/identity/`. It is distinct from `MailAuthProvider` and is not interchangeable with it.

---

## 7. Microsoft Entra OAuth/OIDC Flow

### 7.1 AD-PR13-002: Microsoft Entra Authorization Flow

**Decision:**
PR-1.3 uses Authorization Code + PKCE with OIDC ID-token validation.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

### 7.2 Authorization Code Flow with PKCE

**APPROVED — PER AD-PR13-002**

**Rationale:**
- The repository's `MailAuthProvider` already uses authorization code flow (`exchange_code`).
- PKCE (RFC 7636) eliminates the need for a client secret in the authorization request and protects against authorization-code interception.
- The repository's security posture ("Never trust client claims", "Fail closed") aligns with PKCE's defense-in-depth properties.

**Flow:**

1. **Authorization Initiation** — `GET /auth/entra`
   - Generate `state` (cryptographically random, stored server-side)
   - Generate `nonce` (cryptographically random, stored server-side)
   - Generate `code_verifier` (43-128 character random string)
   - Generate `code_challenge` (BASE64URL(SHA256(code_verifier)))
   - Redirect to Entra ID `/authorize` with:
     - `response_type=code`
     - `client_id`
     - `redirect_uri`
     - `scope=openid profile email offline_access`
     - `state`
     - `nonce`
     - `code_challenge`
     - `code_challenge_method=S256`
    - Store `state`, `nonce`, `code_verifier` in server-side database-backed state store

2. **Callback** — `POST /auth/callback/entra`
   - Public route (no JWT required)
   - Extract `code`, `state` from request
   - Validate `state` against stored value (reject if mismatch)
   - Exchange `code` for tokens with `code_verifier`
   - Validate ID token signature via JWKS
   - Validate ID token claims: `iss`, `aud`, `sub`, `nonce`, `exp`, `iat`
   - Validate `nonce` against stored value
    - Resolve or provision platform identity
    - Create platform session via `AuthenticationService.create_session_tokens()`
    - Return platform tokens in JSON response

3. **Token Exchange**
   - POST to Entra ID `/token` endpoint
   - `grant_type=authorization_code`
   - `code`
   - `redirect_uri`
   - `client_id`
   - `code_verifier`
   - No `client_secret` (PKCE replaces it)

4. **Error Handling**
    - Entra ID returns `error` + `error_description` in callback
    - Callback handler emits `CALLBACK_FAILED` event
    - Return JSON error response

### 7.3 AD-PR13-003: OAuth CSRF and Replay Protection

**Decision:**
State, nonce, and PKCE are all required.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Proposed behavior:**
- Generate `state` as `secrets.token_urlsafe(32)`
- Store in server-side database-backed state store (TTL ≤ 10 minutes)
- Validate on callback; reject mismatched or expired state
- Emit `CALLBACK_FAILED` on state validation failure

### 7.4 Nonce Parameter

**APPROVED — PER AD-PR13-003**

**Rationale:** Replay protection for ID tokens. The repository has no nonce infrastructure.

**Proposed behavior:**
- Generate `nonce` as `secrets.token_urlsafe(32)`
- Store in server-side database-backed state store (same storage as `state`)
- Validate `nonce` claim in ID token matches stored value
- Reject tokens with missing or mismatched nonce
- Emit `CALLBACK_FAILED` on nonce validation failure

### 7.5 PKCE

**APPROVED — PER AD-PR13-003**

**Rationale:** Required for native/mobile app security and recommended for web apps per OAuth 2.0 Security Best Current Practice.

**Proposed behavior:**
- `code_verifier`: 43-128 character random string (ASCII letters, digits, `-`, `.`, `_`, `~`)
- `code_challenge`: BASE64URL(SHA256(code_verifier))
- Store `code_verifier` in server-side database-backed state store until callback
- Include in token exchange request
- Validate against stored verifier

### 7.6 ID Token Validation

**APPROVED — PER AD-PR13-009**

The following ID token validations are required:

| Claim | Validation | Failure Behavior |
| :--- | :--- | :--- |
| `iss` | Must match `https://login.microsoftonline.com/{tenant_id}/v2.0` | Reject token, emit `CALLBACK_FAILED` |
| `aud` | Must match Entra ID client ID | Reject token, emit `CALLBACK_FAILED` |
| `sub` | Must be present, non-empty | Reject token, emit `CALLBACK_FAILED` |
| `nonce` | Must match stored nonce | Reject token, emit `CALLBACK_FAILED` |
| `exp` | Must not be expired | Reject token, emit `CALLBACK_FAILED` |
| `nbf` | Must not be in the future beyond configured clock skew | Reject token, emit `CALLBACK_FAILED` |
| `iat` | Must be reasonable; reject tokens whose `iat` is materially in the future beyond configured clock skew | Reject token, emit `CALLBACK_FAILED` |
| `tid` | Must match expected Entra tenant ID | Reject token, emit `CALLBACK_FAILED` |
| `email` | Used for identity resolution only; not for authorization | Logged in metadata only |

**Clock skew:** Provider token validation must use a bounded clock skew value. The exact value is configuration-dependent. The platform `jwt_clock_skew_seconds` setting governs platform JWTs only; provider token validation must have its own explicitly configured clock skew bound.

**Algorithm:** Only RS256 from Entra ID JWKS keys is accepted. No symmetric or algorithm-confused tokens are trusted.

---

## 8. Provider Token vs Platform Token Boundary

### 8.1 INHERITED SECURITY INVARIANT

From `docs/ai/DECISION_LOG.md:7-12`:

> "Provider authentication is strictly isolated from platform authentication. Mail intelligence relies heavily on background synchronization. If provider credentials (e.g., Microsoft Graph tokens) expire or are revoked, the user's platform session must remain valid so they can be prompted to re-authorize, rather than being abruptly logged out."

From `apps/api/app/auth/tokens.py:34-36`:

```python
FORBIDDEN_ACCESS_TOKEN_CLAIMS = frozenset(
    {"role", "roles", "permissions", "mailbox_ids", "provider_token", "provider_refresh_token"}
)
```

### 8.2 INHERITED SECURITY INVARIANT

Platform JWTs contain ONLY identity/session claims: `sub`, `tid`, `oid`, `sid`, `jti`, `iss`, `aud`, `exp`, `iat`, `nbf`.

Provider tokens (Entra ID access/refresh tokens) MUST NEVER appear in:
- Platform JWT claims
- `AuthenticationContext` fields (beyond `provider` string)
- Security event payloads
- API responses
- Logs

### 8.3 PR-1.3 DESIGN DECISION

Two distinct token types exist after PR-1.3:

| Token Type | Purpose | Storage | Lifetime |
| :--- | :--- | :--- | :--- |
| Platform Access Token (JWT) | API authentication | Client-held opaque value | 15 minutes |
| Platform Refresh Token | Platform session renewal | `refresh_token_families` table | 30 days |
| Entra ID Access Token | Microsoft Graph / Entra API access | Encrypted provider credential store | 1 hour (Entra default) |
| Entra ID Refresh Token | Entra token renewal | Encrypted provider credential store | 90 days (Entra default) |
| OAuth State/Nonce | CSRF/replay protection | Server-side database-backed state store | ≤ 10 minutes |

**Boundary rules:**
1. `AuthenticationService.create_session_tokens()` creates platform tokens only.
2. Provider tokens are stored separately and never touch `TokenService` or `SessionService`.
3. Background workers retrieve provider tokens from credential storage for mail sync.
4. Provider credential refresh does NOT touch `DeviceSession` or platform tokens.
5. Platform session revocation does NOT revoke provider credentials.

---

## 9. Identity Model / Linking

### 9.1 CONFIRMED EXISTING REPOSITORY FACT

The `Identity` model exists at `packages/models/src/mip_models/user.py:97-153`:

```python
class Identity(Base, IdentityMixin, TimestampMixin):
    provider: Mapped[str]  # "microsoft"
    provider_user_id: Mapped[str]  # Entra ID sub claim
    provider_email: Mapped[str | None]
    provider_metadata: Mapped[dict | None]  # JSONB
```

Unique constraint: `uq_identity_provider_user` on `(provider, provider_user_id)`.

`User.identities` relationship has `cascade="all, delete-orphan"`.

### 9.2 PR-1.3 DESIGN DECISION

**provider value:** `"microsoft"` — matches existing `IdentityProvider` enum.

**provider_user_id:** Entra ID `sub` claim (immutable, unique within Entra tenant).

**provider_email:** Entra ID `email` or `preferred_username` claim. May differ from `User.email`.

**provider_metadata:** JSONB storing:
- Entra tenant ID (`tid` claim)
- Entra display name
- OAuth scopes granted
- Raw claims for audit (non-PII only)

### 9.3 Identity Linking Rules

**APPROVED — PER AD-PR13-005**

| Scenario | Behavior |
| :--- | :--- |
| User authenticates via Entra ID, Identity exists, User is active | Link confirmed. Create/renew platform session. |
| User authenticates via Entra ID, Identity exists, User is inactive | Reject authentication. Emit `LOGIN_FAILED`. |
| User authenticates via Entra ID, Identity does NOT exist, JIT provisioning enabled | Create User, create Identity, assign to resolved Tenant/Organization, emit `USER_PROVISIONED` + `IDENTITY_LINKED`. |
| User authenticates via Entra ID, Identity does NOT exist, JIT provisioning disabled | Reject authentication. Emit `LOGIN_FAILED`. |
| Entra identity already linked to DIFFERENT platform User | Reject. Emit `LOGIN_FAILED` with reason "identity_already_linked". Cross-tenant identity reuse is forbidden. |

**Constraint:** One platform `User` may have multiple `Identity` records (e.g., Microsoft + Google in future). One `Identity` record belongs to exactly one `User`.

### 9.4 Unlink Behavior

**DEFERRED / OUT OF SCOPE**

Identity unlinking is deferred to PR-1.3+ or Administration milestone. PR-1.3 creates identities only.

---

## 10. JIT Provisioning

### 10.1 AD-PR13-005: Just-in-Time Provisioning

**Decision:**
JIT provisioning is approved for PR-1.3, subject to the constraints below.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Rules:**
- JIT may create a User only after successful provider authentication.
- Tenant must already exist and be active.
- Organization must already be resolvable from the platform Tenant.
- Email alone MUST NOT be sufficient to link an existing account.
- Existing `Identity(provider="microsoft", provider_user_id=...)` is authoritative.
- Duplicate identity → fail closed.
- Identity already belonging to another User → fail closed.
- JIT must not create arbitrary Tenants or Organizations.
- User creation and Identity creation must occur atomically.
- Membership creation must follow the platform's existing tenant/membership model.
- No cross-tenant automatic linking.

**Implementation prerequisites:**
If the existing repository does not contain sufficient membership/provisioning primitives, they must be implemented before JIT can function.

**If JIT is approved, the following rules apply:**

**APPROVED — PER AD-PR13-005**

1. **Trigger:** Entra ID callback with unknown `(provider="microsoft", provider_user_id)` pair.
2. **User creation:**
   - `email` from Entra ID `email` or `preferred_username` claim
   - `display_name` from Entra ID `name` claim
   - `is_platform_admin` = `false`
   - `is_active` = `true`
3. **Tenant/Organization resolution:** See Section 11.
4. **Membership creation:**
   - Create `Membership` linking User to resolved Tenant
   - Assign default role (`SystemRole.MEMBER`)
   - `is_active` = `true`
5. **Identity creation:**
   - Create `Identity` linking User to Entra identity
6. **Failure/rollback:**
   - If tenant resolution fails, reject authentication — do not create orphaned User
   - If membership creation fails, reject authentication — do not create orphaned User
   - All database operations must occur in a single transaction

**Alternative (if JIT is NOT approved):**
- Only pre-provisioned `User` + `Identity` records allow Entra authentication
- Entra identities without matching `Identity` records receive `LOGIN_FAILED`

---

## 11. Tenant / Organization Resolution

### 11.1 Tenant Resolution Strategy

**APPROVED — PER AD-PR13-004**

The Entra tenant ID is the authoritative provider tenant identifier. This decision is resolved and approved. Implementation must use the approved Entra tenant → platform Tenant mapping mechanism.

- Unknown Entra tenant → authentication failure.
- Inactive platform Tenant → authentication failure.
- Ambiguous mapping → authentication failure.
- Email domain MUST NOT independently authorize tenant membership.
- The callback must never allow the client to supply `tenant_id` or `organization_id`.
- `organization_id` is resolved server-side from the platform Tenant.
- Cross-tenant identity linking is prohibited.

### 11.2 CONFIRMED EXISTING REPOSITORY FACT

From `packages/models/src/mip_models/organization.py:73-77`:

```python
domain: Mapped[str | None] = mapped_column(
    String(255),
    nullable=True,
    comment="Verified email domain for JIT provisioning",
)
domain_verified_at: Mapped[datetime | None] = mapped_column(
    nullable=True,
    comment="When the domain was verified via DNS TXT record",
)
```

The `Organization.domain` field exists and is explicitly documented for JIT provisioning, but no domain verification or matching logic exists in the codebase.

### 11.3 AD-PR13-004: Entra Tenant to Platform Tenant Resolution

**Decision:**
The Entra tenant ID is the primary identity-provider tenant identifier.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Rules:**
- Entra tenant ID must map to exactly one platform Tenant.
- Unknown Entra tenant → authentication failure.
- Inactive platform Tenant → authentication failure.
- Ambiguous mapping → authentication failure.
- Email domain MUST NOT independently authorize tenant membership.
- The callback must never allow the client to supply `tenant_id` or `organization_id`.
- `organization_id` is resolved server-side from the platform Tenant.
- Cross-tenant identity linking is prohibited.

If the repository currently lacks the mapping needed to implement this, the required schema/configuration change must be documented as an implementation prerequisite rather than pretending it exists.

### 11.4 Security Requirement

**INHERITED SECURITY INVARIANT**

From `apps/api/app/auth/middleware.py:254-299`:

Middleware validates `DeviceSession.tenant_id == JWT.tid` and `Tenant.organization_id == JWT.oid`.

**PR-1.3 requirement:** A provider-authenticated identity must never obtain a platform `DeviceSession` for a `Tenant` it is not authorized to enter. Tenant resolution must be deterministic and must not allow cross-tenant identity reuse.

**APPROVED — PER AD-PR13-004**

The Entra tenant ID is the primary identity-provider tenant identifier.

**Rules:**
- Entra tenant ID must map to exactly one platform Tenant.
- Unknown Entra tenant → authentication failure.
- Inactive platform Tenant → authentication failure.
- Ambiguous mapping → authentication failure.
- Email domain MUST NOT independently authorize tenant membership.
- The callback must never allow the client to supply `tenant_id` or `organization_id`.
- `organization_id` is resolved server-side from the platform Tenant.
- Cross-tenant identity linking is prohibited.

If the repository currently lacks the mapping needed to implement this, the required schema/configuration change must be documented as an implementation prerequisite rather than pretending it exists.

---

## 12. Provider Credential Storage

### 12.1 CONFIRMED EXISTING REPOSITORY FACT

The `ProviderCredential` model exists at `packages/models/src/mip_models/mail.py:120-174`:

```python
class ProviderCredential(Base, IdentityMixin, TimestampMixin):
    """Encrypted OAuth tokens for mail provider access."""
    mail_account_id: Mapped[uuid.UUID]  # FK to mail_accounts (UNIQUE)
    tenant_id: Mapped[uuid.UUID]
    encrypted_access_token: Mapped[bytes]  # AES-256-GCM encrypted
    encrypted_refresh_token: Mapped[bytes]  # AES-256-GCM encrypted
    token_expires_at: Mapped[datetime]
    scopes: Mapped[list[str] | None]
    encryption_key_id: Mapped[str]  # DEK identifier
```

This model is scoped to `MailAccount` via `mail_account_id`. It is designed for mail provider OAuth tokens (Microsoft Graph mail access), not identity provider tokens.

### 12.2 Tradeoff Analysis

| Approach | Pros | Cons |
| :--- | :--- | :--- |
| **Reuse `ProviderCredential`** | No new table; existing encryption columns | Tightly coupled to `MailAccount`; identity credentials are not mail credentials; schema mismatch |
| **Extend `ProviderCredential`** | Reuses storage mechanism | Requires polymorphic association or nullable FK; violates single-responsibility principle |
| **New `IdentityProviderCredential` model** | Clean separation; purpose-built for identity provider tokens | Requires new table, new migration, new encryption service |

### 12.3 AD-PR13-007: Identity Provider Credential Storage

**Decision:**
New `IdentityProviderCredential` model. Separate from mail-scoped `ProviderCredential`.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Proposed schema:**

```python
class IdentityProviderCredential(Base, IdentityMixin, TimestampMixin):
    """Encrypted OAuth tokens for an external identity provider."""
    __tablename__ = "identity_provider_credentials"
    
    identity_id: Mapped[uuid.UUID]  # FK to identities.id, UNIQUE
    tenant_id: Mapped[uuid.UUID]  # FK to tenants.id, denormalized for query safety
    provider: Mapped[str]  # "microsoft"
    encrypted_access_token: Mapped[bytes]  # AES-256-GCM encrypted
    encrypted_refresh_token: Mapped[bytes]  # AES-256-GCM encrypted
    token_expires_at: Mapped[datetime]
    scopes: Mapped[list[str] | None]
    encryption_key_id: Mapped[str]  # DEK identifier
    revoked_at: Mapped[datetime | None]  # NULL = active
```

Repository-level tenant isolation must be enforced for all queries against this table.

---

## 13. Encryption

### 13.1 CONFIRMED EXISTING REPOSITORY FACT

`SECURITY.md:14` states:

> "OAuth tokens are encrypted at rest using envelope encryption."

However, the investigation found **no encryption service implementation** in the codebase. The `ProviderCredential` model has columns typed as `BYTEA` with comments indicating "AES-256-GCM encrypted" and `encryption_key_id` referencing a Data Encryption Key (DEK), but no actual encryption/decryption code exists.

### 13.2 PR-1.3 SECURITY REQUIREMENT

PR-1.3 MUST store Entra ID access and refresh tokens encrypted at rest. Plaintext provider tokens must never be written to the database.

### 13.3 AD-PR13-008: Provider Credential Encryption

**Decision:**
Full envelope encryption.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**CURRENT STATE:**
No complete provider credential encryption service currently exists. The repository claims OAuth tokens are encrypted at rest (`SECURITY.md:14`), but no encryption/decryption implementation was found.

**PR-1.3 REQUIREMENT:**
An encryption service must be implemented before provider credentials are persisted.

**Requirements:**
- OAuth provider credentials encrypted at rest.
- DEK encrypts credential payload.
- KEK/master key protects DEK.
- `encryption_key_id` identifies the active key material.
- AES-256-GCM authenticated encryption.
- Plaintext provider tokens never persisted.
- Plaintext provider tokens never logged.
- Plaintext provider tokens never included in SecurityEvent metadata.
- Decryption failure fails closed.
- Key rotation must be possible without exposing plaintext outside the credential service boundary.

---

## 14. JWKS / Entra ID Token Validation

### 14.1 CONFIRMED EXISTING REPOSITORY FACT

From `apps/api/app/auth/tokens.py:30`:

```python
ALLOWED_JWT_ALGORITHMS = frozenset({"HS256"})
```

The platform only supports HS256 for its own JWTs. No JWKS fetching, no asymmetric JWT verification, no key rotation infrastructure exists.

### 14.2 AD-PR13-009: Microsoft Entra ID Token Validation

**Decision:**
Provider-specific JWKS validation. Platform TokenService is not modified.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Requirements:**
- signature validated against Entra ID JWKS
- allowed asymmetric algorithm: RS256 only
- issuer validated
- audience validated
- expiration validated
- not-before (`nbf`) validated
- issued-at (`iat`) reasonableness validated (reject tokens materially in the future beyond configured clock skew)
- nonce validated
- tenant/issuer consistency validated
- key ID (`kid`) validated
- JWKS key rotation handled
- Clock skew: bounded, explicitly configured for provider token validation (platform `jwt_clock_skew_seconds` does not apply)
- Do not claim this infrastructure exists today

---

## 15. Callback Architecture

### 15.1 AD-PR13-011: Callback Token Exposure Prevention

**Decision:**
Platform access/refresh tokens must never be exposed in URLs, query parameters, path parameters, redirect URLs, logs, referrers, or security events.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

`POST /auth/callback/entra` must return a server-generated authentication response directly. Provider tokens must never be placed in redirect URLs. If a frontend/browser handoff is required later, it must be deferred and designed separately.

### 15.2 AD-PR13-006: OAuth State Storage

**Decision:**
Server-side database-backed state store.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Requirements:**
- Dedicated OAuth authentication-state record/model if needed.
- Contains only the minimum state necessary:
  - state identifier/hash
  - provider
  - redirect context
  - PKCE verifier or securely associated PKCE material
  - nonce
  - creation/expiry time
  - request_id if useful for correlation
  - consumed timestamp
- Short expiration.
- Single use.
- Atomic consumption.
- Invalid/expired/consumed state → fail closed.
- State values never logged.
- PKCE verifier never exposed to the client.
- State cannot encode trusted tenant/user authorization data.

Do NOT use Redis, client-controlled state, unsigned state, or long-lived state JWTs.

### 15.3 Security Requirements

- Callback must validate `state` before exchanging authorization code
- Callback must validate `nonce` after ID token validation
- Callback must never log authorization codes or tokens
- Callback must never expose tokens in error messages
- Callback returns JSON response; provider tokens never placed in URLs, query parameters, path parameters, redirect URLs, logs, referrers, or security events
- Redirect URI is server configuration; exact configured URI only, no wildcard matching, no client-supplied arbitrary redirect URI
- Failed callbacks return JSON error response; no raw error rendering

---

## 16. AuthenticationService Integration

### 16.1 CONFIRMED EXISTING REPOSITORY FACT

From `apps/api/app/auth/service.py:42-49`:

```python
class AuthenticationService:
    """Provider-agnostic orchestration for platform sessions and tokens.
    
    This service deliberately does not know how Microsoft, Google, Okta, or
    another provider authenticates a user. PR-1.3 provider adapters will call
    into this service after they have resolved a valid platform user, tenant,
    and organization."""
```

**Existing API:**

```python
async def create_session_tokens(
    self,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    organization_id: uuid.UUID,
    ip_address: str | None = None,
    user_agent: str | None = None,
    remember_me: bool = False,
    request_id: str | None = None,
) -> AuthenticationResult:
    ...
```

### 16.2 PR-1.3 DESIGN DECISION

**Provider adapter contract:**

The Entra ID provider adapter MUST:
1. Validate the Entra ID callback (state, nonce, ID token, access token)
2. Resolve the platform `User` (existing or JIT-provisioned)
3. Resolve `tenant_id` and `organization_id`
4. Call `AuthenticationService.create_session_tokens(user_id, tenant_id, organization_id, ...)`
5. Receive `AuthenticationResult` containing platform access token, refresh token, and session ID
6. Store provider credentials separately (via `ProviderCredentialStorage`)
  7. Return platform tokens to the callback handler for response to client

**AuthenticationService does NOT need modification for PR-1.3.**

The existing `AuthenticationContext.provider` field should be populated by the callback handler or a dedicated provider session creation service, not by `AuthenticationService`.

---

## 17. AuthenticationContext

### 17.1 CONFIRMED EXISTING REPOSITORY FACT

From `apps/api/app/auth/context.py:18-42`:

```python
@dataclass(frozen=True, slots=True)
class AuthenticationContext:
    ...
    authentication_method: str = "session"
    provider: str | None = None  # None for platform auth
    ...
```

### 17.2 AD-PR13-010: AuthenticationContext Provider Persistence

**Decision:**
`provider` is derived server-side from the authenticated identity, never client-supplied, and never embedded in the platform JWT.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Mechanism:**
Platform JWT claims are frozen and do NOT contain provider information. On subsequent authenticated requests, the middleware must derive `provider` from the server-side identity/session relationship, e.g.:

platform JWT → DeviceSession → associated authenticated platform identity → provider

If `DeviceSession` currently lacks the required association, the minimum schema/model relationship required must be identified and implemented.

`authentication_method` remains `"session"` unless there is a repository-backed reason to change it.

The callback handler setting `provider` on the initial request is NOT sufficient for subsequent requests.

---

## 18. Provider Credential Refresh

### 18.1 INHERITED SECURITY INVARIANT

From `docs/ai/DECISION_LOG.md:7-12`:

> "If provider credentials (e.g., Microsoft Graph tokens) expire or are revoked, the user's platform session must remain valid."

### 18.2 AD-PR13-012: Provider Credential Refresh Isolation

**Decision:**
Entra refresh token refreshes Entra credentials only. It never rotates the platform DeviceSession refresh token. Background refresh is NOT required for initial PR-1.3.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Rules:**
- Provider credential failure does not revoke platform `DeviceSession`.
- Refresh logic belongs to the provider credential service/adapter.
- On-demand refresh may be specified.
- Background refresh can be explicitly deferred.
- Provider refresh failure must emit a security/operational event, preserve the platform session, mark provider credential state appropriately, and allow later reauthorization.
- User-facing notification for refresh failure can be deferred.

---

## 19. Security Events

### 19.1 CONFIRMED EXISTING REPOSITORY FACT

From `apps/api/app/auth/events.py:42-84`:

```python
class SecurityEventType(StrEnum):
    LOGIN_STARTED = "login_started"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    CALLBACK_RECEIVED = "callback_received"
    CALLBACK_FAILED = "callback_failed"
    USER_PROVISIONED = "user_provisioned"
    IDENTITY_LINKED = "identity_linked"
```

### 19.2 PR-1.3 DESIGN DECISION

All provider authentication outcomes use existing `SecurityEventType` values. No new event types are required.

| Event | When | Outcome | Required Fields | Prohibited Fields |
| :--- | :--- | :--- | :--- | :--- |
| `LOGIN_STARTED` | User initiates `/auth/entra` | SUCCESS | `provider`, `request_id` | tokens, PII |
| `LOGIN_SUCCEEDED` | Entra ID auth succeeds, platform session created | SUCCESS | `user_id`, `tenant_id`, `session_id`, `provider`, `request_id` | tokens |
| `LOGIN_FAILED` | Entra ID auth fails at any step | FAILURE | `reason`, `provider`, `request_id` | tokens, `user_id` (if not known) |
| `CALLBACK_RECEIVED` | Valid callback received from Entra ID | SUCCESS | `provider`, `request_id` | tokens |
| `CALLBACK_FAILED` | Callback validation fails (state, nonce, token, claims) | FAILURE | `reason`, `provider`, `request_id` | tokens |
| `USER_PROVISIONED` | JIT creates new User from Entra identity | SUCCESS | `user_id`, `tenant_id`, `provider`, `request_id` | tokens |
| `IDENTITY_LINKED` | Entra identity linked to existing User | SUCCESS | `user_id`, `provider`, `request_id` | tokens |

**Event sequences:**

Successful login:
1. `LOGIN_STARTED` (SUCCESS) — authorization initiation
2. `CALLBACK_RECEIVED` (SUCCESS) — valid callback received
3. `IDENTITY_LINKED` (SUCCESS) — identity linked to existing User, OR `USER_PROVISIONED` (SUCCESS) — JIT created new User
4. `LOGIN_SUCCEEDED` (SUCCESS) — platform session created

Failed authorization:
1. `LOGIN_STARTED` (SUCCESS) — authorization initiation
2. `CALLBACK_FAILED` (FAILURE) — specific validation failure reason
3. `LOGIN_FAILED` (FAILURE) — authentication rejected

Invalid state:
1. `CALLBACK_RECEIVED` (SUCCESS)
2. `CALLBACK_FAILED` (FAILURE) — reason: invalid_state
3. `LOGIN_FAILED` (FAILURE)

Invalid nonce:
1. `CALLBACK_RECEIVED` (SUCCESS)
2. `CALLBACK_FAILED` (FAILURE) — reason: invalid_nonce
3. `LOGIN_FAILED` (FAILURE)

Invalid ID token:
1. `CALLBACK_RECEIVED` (SUCCESS)
2. `CALLBACK_FAILED` (FAILURE) — reason: invalid_token
3. `LOGIN_FAILED` (FAILURE)

Identity collision:
1. `CALLBACK_RECEIVED` (SUCCESS)
2. `LOGIN_FAILED` (FAILURE) — reason: identity_already_linked

JIT provisioning:
1. `CALLBACK_RECEIVED` (SUCCESS)
2. `USER_PROVISIONED` (SUCCESS) — new User created
3. `IDENTITY_LINKED` (SUCCESS) — Entra identity linked
4. `LOGIN_SUCCEEDED` (SUCCESS) — platform session created

Credential refresh failure:
- Emit operational/security event as appropriate; do not revoke platform session.

**All events must include `request_id` from `RequestIdMiddleware`.**

**Token leakage restrictions:** Raw Entra ID access tokens, refresh tokens, authorization codes, ID token payloads, PKCE verifiers, state values, and nonces must never appear in security events, logs, or API responses.

---

## 20. Threat Model

### 20.1 Scope

This threat model covers PR-1.3 Provider Integration for Microsoft Entra ID. It builds on the PR-1.2.x security baseline and addresses threats specific to external identity provider integration.

### 20.2 Threat Analysis

| Threat | Attack Path | Existing Mitigation | PR-1.3 Mitigation | Residual Risk | Validation/Test |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Authorization code interception** | Attacker intercepts authorization code before callback | HTTPS enforcement, short-lived codes | PKCE ensures code is useless without verifier; state validation prevents CSRF | Low if PKCE + state implemented | Test: code without matching verifier rejected |
| **State/CSRF attack** | Attacker tricks user into authorizing with attacker's state | None — no state infrastructure exists | State parameter validated server-side; mismatched state rejected with `CALLBACK_FAILED` | Low | Test: mismatched state rejected |
| **Nonce replay** | Attacker replays captured ID token | None — no nonce infrastructure | Nonce validated server-side; single-use or TTL-bound storage | Low | Test: replayed nonce rejected |
| **Authorization code replay** | Attacker replays captured authorization code | None — codes are single-use by Entra | Entra ID enforces single-use; PR-1.3 validates state/nonce before exchange | Low | Entra ID enforces; PR-1.3 validates state |
| **ID-token substitution** | Attacker substitutes ID token from different tenant | None — no ID token validation exists | Issuer, audience, nonce, and signature validated against expected values | Low | Test: wrong issuer/audience rejected |
| **Issuer confusion** | Attacker uses ID token from different Entra tenant | None | `iss` claim validated against expected Entra tenant format | Low | Test: invalid issuer rejected |
| **Audience confusion** | Attacker uses ID token intended for different client | None | `aud` claim validated against Entra client ID | Low | Test: wrong audience rejected |
| **JWKS/key confusion** | Attacker exploits key cache poisoning | None | JWKS fetched from canonical Entra discovery endpoint; keys cached with TTL; algorithm restricted to RS256 | Low | Test: fetch from wrong JWKS endpoint rejected |
| **Malicious/incorrect redirect URI** | Attractor redirects authorization code to attacker | None | Redirect URI must be pre-registered with Entra ID; callback validates against configured URI | Low | Entra ID enforces; callback validates |
| **Token leakage** | Tokens exposed in logs, errors, or responses | Platform tokens never logged; `FORBIDDEN_ACCESS_TOKEN_CLAIMS` | Provider tokens never logged, never returned in responses, never in security events | Low | Test: tokens absent from logs/responses |
| **Refresh-token theft** | Attacker steals Entra refresh token | None — no provider tokens stored yet | Tokens encrypted at rest; never logged; stored separately from platform tokens | Medium — depends on encryption implementation | Test: encrypted storage, no plaintext in DB |
| **Provider credential compromise** | Attacker gains access to encrypted provider credentials | None | Encryption at rest; `tenant_id` isolation; `revoked_at` support | Medium — depends on encryption implementation | Test: encrypted fields not readable without key |
| **Account takeover through unsafe identity linking** | Attacker links Entra identity to victim's platform account | `uq_identity_provider_user` prevents duplicate identities | Cross-tenant linking forbidden; existing identity must match active User; JIT provisioning creates new User, does not link to existing | Low | Test: cannot link to different User/tenant |
| **Email-based account takeover** | Attacker uses similar email to claim victim's account | None — email is not a security boundary | Identity binding uses `provider_user_id` (Entra `sub`), not email; email is for resolution/metadata only | Low | Test: different `sub` with same email treated as distinct identity |
| **Cross-tenant identity confusion** | Attacker authenticates in Entra tenant A, gains access to platform tenant B | None — no cross-tenant provider auth exists | Tenant resolution is deterministic; identity is scoped to resolved tenant; `DeviceSession.tenant_id` enforced by middleware | Low | Test: Entra tenant A cannot access platform tenant B |
| **JIT provisioning abuse** | Attacker creates platform account via unapproved Entra tenant | None — no JIT exists yet | JIT must be explicitly approved; if approved, tenant resolution must be deterministic and non-ambiguous | Medium if JIT approved without controls | Test: unknown Entra tenant rejected if no mapping |
| **Duplicate identity races** | Concurrent callbacks create duplicate Identity records | `uq_identity_provider_user` prevents duplicates | Database unique constraint; idempotent identity lookup before creation | Low | Test: concurrent callbacks do not create duplicate identities |
| **Revoked provider credentials** | Revoked Entra token used to access platform | None | Revoked provider credentials do not affect platform `DeviceSession`; workers detect and handle refresh failure | Low | Test: revoked credential does not invalidate platform session |
| **Provider outage** | Entra ID unavailable | None | Callback fails gracefully; platform unaffected; security event emitted | Low | Test: provider timeout emits `CALLBACK_FAILED` |
| **Compromised Entra tenant** | Attacker controls Entra tenant, provisions malicious identities | None | Platform tenant mapping controls which Entra tenants can provision users; explicit mapping preferred | Medium — depends on tenant resolution strategy | Test: compromised Entra tenant cannot provision outside mapped platform tenant |
| **Session fixation** | Attacker forces victim to use attacker's session | Platform `DeviceSession` created after auth; stable session ID | New `DeviceSession` created per successful authentication; no session ID reuse | Low | Test: new session ID on each auth |
| **Callback endpoint abuse** | Attacker sends crafted callback requests | None — endpoint does not exist yet | Public route with strict validation; no business logic before validation; rate limiting deferred | Low | Test: malformed callback rejected |
| **Logging/telemetry leakage** | Provider tokens leaked to logs, metrics, or telemetry | Platform tokens never logged; `SecurityEvent` excludes tokens | Provider tokens explicitly prohibited from all events; logging rules enforced | Low | Test: verify no tokens in structured logs |
| **Open redirect behavior** | Attacker uses callback to redirect to malicious site | None — no redirect logic exists | Callback returns JSON response; no redirect URL containing tokens | Low | Test: callback returns JSON, no token in response |
| **Missing nbf validation** | Attacker uses ID token with future `nbf` to bypass time-based restrictions | None — no provider token validation exists | `nbf` claim validated; tokens with future `nbf` beyond clock skew rejected with `CALLBACK_FAILED` | Low | Test: future nbf rejected |
| **Clock skew exploitation** | Attacker exploits time drift to use expired or not-yet-valid tokens | None — no provider token validation exists | Bounded clock skew configured for provider token validation; `exp`, `nbf`, `iat` validated against skew | Low | Test: tokens outside skew window rejected |
| **Future iat exploitation** | Attacker uses token with `iat` materially in the future | None — no provider token validation exists | `iat` reasonableness validated; tokens with future `iat` beyond clock skew rejected | Low | Test: future iat rejected |
| **Token leakage through redirect** | Provider tokens exposed in redirect URLs or query parameters | None — no provider auth exists | Callback returns JSON response; provider tokens never placed in URLs, query parameters, or redirects | Low | Test: no tokens in callback response or redirects |
| **State storage compromise** | Attacker reads or modifies stored OAuth state/nonce/PKCE verifier | None — no state infrastructure exists | State stored in database-backed state store with short TTL; single-use atomic consumption; state values never logged | Low — depends on database security | Test: reused/expired state rejected |
| **Identity linking takeover** | Attacker links their Entra identity to victim's platform account via identity collision | `uq_identity_provider_user` prevents duplicates | Existing `Identity(provider, provider_user_id)` is authoritative; duplicate identity fails closed; identity already linked to another User fails closed | Low | Test: cannot claim existing identity |
| **Cross-tenant identity linking** | Attacker links Entra identity from tenant A to platform account in tenant B | None — no cross-tenant provider auth exists | Entra tenant ID must map to exactly one platform Tenant; cross-tenant linking prohibited; callback never accepts client-supplied tenant_id | Low | Test: cross-tenant identity rejected |

---

## 21. Security Requirements

### 21.1 PR13-SR-001: State Validation

**Requirement:** The Entra ID callback must validate the `state` parameter against a server-side stored value before processing the authorization code.

**Rationale:** CSRF protection. Prevents attackers from initiating authorization flows on behalf of users.

**Affected Component:** Entra callback handler, state storage

**Validation Method:** Unit test: mismatched or expired state rejected with `CALLBACK_FAILED` event

---

### 21.2 PR13-SR-002: Nonce Validation

**Requirement:** The Entra ID callback must validate the `nonce` claim in the ID token against a server-side stored value.

**Rationale:** Replay protection. Prevents attackers from replaying captured ID tokens.

**Affected Component:** Entra ID token validator

**Validation Method:** Unit test: replayed or missing nonce rejected with `CALLBACK_FAILED` event

---

### 21.3 PR13-SR-003: PKCE Enforcement

**Requirement:** The authorization initiation must generate a `code_verifier` and `code_challenge`, and the callback must validate the `code_verifier` during token exchange.

**Rationale:** Authorization-code interception protection. Eliminates need for client secret in authorization request.

**Affected Component:** Entra authorization initiator, callback handler

**Validation Method:** Unit test: token exchange without correct verifier rejected

---

### 21.4 PR13-SR-004: ID Token Signature Validation

**Requirement:** ID tokens must be validated using the Entra ID JWKS endpoint. Only RS256 signatures from Entra ID keys are accepted.

**Rationale:** Forgery prevention. Ensures ID token was actually issued by Entra ID.

**Affected Component:** Entra ID token validator

**Validation Method:** Unit test: tampered signature rejected; test with mock JWKS

---

### 21.5 PR13-SR-005: ID Token Claim Validation

**Requirement:** ID tokens must contain valid `iss`, `aud`, `sub`, `nonce`, `exp`, `iat`, and `tid` claims matching expected values.

**Rationale:** Token substitution and replay prevention.

**Affected Component:** Entra ID token validator

**Validation Method:** Unit test: each invalid claim variant rejected

---

### 21.6 PR13-SR-006: Provider Token Isolation

**Requirement:** Entra ID access tokens, refresh tokens, authorization codes, and ID token payloads must never appear in platform JWTs, `AuthenticationContext` (beyond `provider` string), security events, API responses, or logs.

**Rationale:** Inherited security invariant. Prevents token leakage across trust boundaries.

**Affected Component:** All provider auth components

**Validation Method:** Security test: provider tokens absent from JWT payloads, event payloads, and log output

---

### 21.7 PR13-SR-007: Provider Credential Encryption

**Requirement:** Entra ID access and refresh tokens must be encrypted at rest using AES-256-GCM before database storage.

**Rationale:** Protects provider tokens at rest. Aligns with repository security policy.

**Affected Component:** `IdentityProviderCredential` storage, encryption service

**Validation Method:** Integration test: database contains ciphertext, not plaintext; decryption requires encryption key

---

### 21.8 PR13-SR-008: Cross-Tenant Identity Isolation

**Requirement:** An Entra identity linked to one platform `User` must never be linked to a `User` in a different platform `Tenant`. Cross-tenant identity reuse is forbidden.

**Rationale:** Prevents privilege escalation and data leakage across tenants.

**Affected Component:** Identity linking logic

**Validation Method:** Integration test: Entra identity linked to tenant A cannot authenticate for tenant B

---

### 21.9 PR13-SR-009: Platform Session Isolation from Provider Credentials

**Requirement:** Provider credential expiration, revocation, or refresh failure must never invalidate the platform `DeviceSession`.

**Rationale:** Inherited architectural decision. Platform session and provider credentials have independent lifecycles.

**Affected Component:** Provider credential refresh, session management

**Validation Method:** Integration test: revoked provider credential does not affect platform session validity

---

### 21.10 PR13-SR-010: Request ID Propagation

**Requirement:** All provider authentication security events must include `request_id` from `RequestIdMiddleware`.

**Rationale:** Inherited from PR-1.2.5. Enables correlation of provider auth events with platform request logs.

**Affected Component:** All provider auth event emission points

**Validation Method:** Unit test: every provider auth event contains non-null `request_id`

---

### 21.11 PR13-SR-011: Fail-Closed Callback Handling

**Requirement:** Any validation failure during callback processing must result in `CALLBACK_FAILED` event and safe JSON error response. No partial authentication or session creation on validation failure.

**Rationale:** Prevents authentication bypass through error paths.

**Affected Component:** Callback handler

**Validation Method:** Unit test: each validation failure (state, nonce, token, claims) emits `CALLBACK_FAILED` and does not create session

---

### 21.12 PR13-SR-012: Tenant Resolution Determinism

**Requirement:** Tenant/Organization resolution for provider-authenticated identities must be deterministic. If no unique mapping exists, authentication must fail.

**Rationale:** Prevents ambiguous or arbitrary tenant assignment.

**Affected Component:** Tenant resolution logic

**Validation Method:** Unit test: ambiguous tenant mapping rejected; zero matches rejected; multiple matches rejected

---

---

## 22. API Contracts

### 22.1 GET /auth/entra

**Method:** GET  
**Path:** `/auth/entra`  
**Authentication:** None (public route)  
**Purpose:** Initiate Microsoft Entra ID authorization flow

**Query Parameters:**
| Parameter | Required | Description |
| :--- | :--- | :--- |
| `state` | Auto-generated | CSRF protection (returned to client for callback validation) |

**Behavior:**
1. Generate `state`, `nonce`, `code_verifier`, `code_challenge`
2. Store `state`, `nonce`, `code_verifier` in server-side database-backed state store with TTL ≤ 10 minutes
3. Redirect to Entra ID `/authorize` endpoint with PKCE parameters
4. Emit `LOGIN_STARTED` security event

**Response:**
- Success: HTTP 302 redirect to Entra ID authorization endpoint
- Failure: HTTP 400 with error response (frontend misconfiguration)

**Security Events:** `LOGIN_STARTED` (SUCCESS)

---

### 22.2 POST /auth/callback/entra

**Method:** POST  
**Path:** `/auth/callback/entra`  
**Authentication:** None (public route)  
**Purpose:** OAuth2 callback for Entra ID authorization code exchange

**Request Body:**
| Field | Type | Description |
| :--- | :--- | :--- |
| `code` | string | Authorization code from Entra ID |
| `state` | string | CSRF protection token |

**Behavior:**
1. Validate `state` against stored value
2. Exchange `code` for tokens using `code_verifier`
3. Validate ID token signature via JWKS
4. Validate ID token claims (`iss`, `aud`, `sub`, `nonce`, `exp`, `iat`, `tid`)
5. Resolve or provision platform identity
6. Create platform session via `AuthenticationService.create_session_tokens()`
7. Store provider credentials
8. Emit appropriate security events
9. Return platform tokens in JSON response

**Response (success):**
- HTTP 200 JSON containing:
  - `access_token` (platform JWT)
  - `refresh_token` (platform refresh token)
  - `token_type=bearer`
  - `expires_in`

**Response (failure):**
- HTTP 400/401 JSON with error details
- Security event: `CALLBACK_FAILED`

**Error Codes:**
| Error | Condition |
| :--- | :--- |
| `invalid_state` | State parameter mismatch or expired |
| `invalid_grant` | Authorization code invalid or expired |
| `invalid_token` | ID token validation failed |
| `identity_not_found` | No matching Identity and JIT disabled |
| `tenant_resolution_failed` | Cannot determine platform tenant |
| `provider_authentication_failed` | Generic provider error |

**Security Events:**
- `CALLBACK_RECEIVED` (SUCCESS) — valid callback received
- `CALLBACK_FAILED` (FAILURE) — any validation failure
- `LOGIN_SUCCEEDED` (SUCCESS) — platform session created
- `LOGIN_FAILED` (FAILURE) — authentication rejected
- `USER_PROVISIONED` (SUCCESS) — JIT created new User
- `IDENTITY_LINKED` (SUCCESS) — Entra identity linked to existing User

---

## 23. Database / Migration Requirements

### 23.1 Existing Tables Reused

| Table | Purpose | Status |
| :--- | :--- | :--- |
| `users` | Platform users | EXISTS |
| `identities` | External identity bindings | EXISTS — has `provider`, `provider_user_id`, `provider_email`, `provider_metadata` |
| `tenants` | Data isolation boundary | EXISTS |
| `organizations` | Billing entity | EXISTS |
| `device_sessions` | Platform sessions | EXISTS — provider auth creates same DeviceSession |
| `refresh_token_families` | Platform token epochs | EXISTS |
| `audit_logs` | Security event persistence (model exists, sink not implemented) | EXISTS |

### 23.2 New Tables Required

| Table | Purpose | Status |
| :--- | :--- | :--- |
| `identity_provider_credentials` | Encrypted Entra ID access/refresh tokens | **APPROVED NEW** |
| `oauth_states` | OAuth state/nonce/PKCE storage | **APPROVED NEW** |

### 23.3 New Columns Required

| Table | Column | Purpose |
| :--- | :--- | :--- |
| `oauth_states` | `state`, `nonce`, `code_verifier`, `created_at` | CSRF/replay protection storage |

### 23.4 Indexes and Constraints

| Target | Index/Constraint | Purpose |
| :--- | :--- | :--- |
| `identity_provider_credentials` | UNIQUE on `identity_id` | One credential set per identity |
| `identity_provider_credentials` | INDEX on `tenant_id` | Tenant-scoped credential queries |
| `identity_provider_credentials` | INDEX on `token_expires_at` | Refresh scheduling |
| `oauth_states` | UNIQUE on `state` | Fast state validation |
| `oauth_states` | INDEX on `created_at` | TTL-based cleanup |

### 23.5 Tenant Isolation

All new tables must enforce tenant isolation at the repository layer. `identity_provider_credentials.tenant_id` is denormalized from the linked `Identity` -> `User` -> `Membership` -> `Tenant` chain for query safety.

### 23.6 Migration Notes

- No migration is created in this EDD.
- Migration must preserve existing `identities` table data.
- Migration must include `downgrade()` per ENGINEERING_RULES.md.
- Backfill strategy for existing data: not required (new table).

### 23.7 Implementation Prerequisites

The following schema/configuration changes are required for PR-1.3. If any of these do not exist in the repository, they must be implemented before the dependent feature can function.

**OAuth State Persistence (required by AD-PR13-006):**
- A database-backed state record/model is required.
- Must store: `state` (unique), `nonce`, `code_verifier`, `provider`, `created_at`, `expires_at`, `consumed_at`.
- `state` must be unique and single-use.
- State must expire within ≤ 10 minutes.
- Consumption must be atomic.
- State/nonce/PKCE verifier must never be stored in client-controlled state, unsigned JWTs, or Redis/in-memory cache.

**IdentityProviderCredential (required by AD-PR13-007):**
- New `identity_provider_credentials` table as specified in Section 12.3.
- Must store encrypted access/refresh tokens, `encryption_key_id`, `token_expires_at`, `scopes`, `tenant_id`, `identity_id`.
- Tenant isolation enforced at repository layer.

**Entra Tenant → Platform Tenant Mapping (required by AD-PR13-004):**
- The repository currently lacks a mechanism to map an Entra tenant ID to a platform `Tenant`.
- Implementation must provide this mapping via configuration or a dedicated persistence mechanism.
- Unknown Entra tenant → authentication failure.
- Inactive platform Tenant → authentication failure.
- Ambiguous mapping → authentication failure.
- Email domain MUST NOT independently authorize tenant membership.

**DeviceSession → Identity/Provider Derivation (required by AD-PR13-010):**
- Platform JWT claims are frozen and do NOT contain provider information.
- On subsequent authenticated requests, middleware must derive `provider` from the server-side identity/session relationship.
- If `DeviceSession` currently lacks the required association to resolve the authenticated `Identity`, the minimum schema/model relationship required must be added.
- The derivation path is: platform JWT → DeviceSession → associated authenticated platform identity → provider.
- `authentication_method` remains `"session"` unless a repository-backed reason changes it.

---

## 24. Configuration

### 24.1 CONFIRMED EXISTING SETTINGS

From `apps/api/app/common/config.py:82-95`:

```python
# --- Authentication ---
jwt_signing_secret: str
jwt_algorithm: str = "HS256"
jwt_issuer: str = "mail-intelligence-platform"
jwt_audience: str = "mail-intelligence-api"
jwt_access_token_expire_minutes: int = 15
jwt_clock_skew_seconds: int = 60
jwt_refresh_token_expire_days: int = 30
session_idle_timeout_hours: int = 8
session_absolute_timeout_days: int = 30
session_remember_me_days: int = 90
```

### 24.2 CONFIRMED EXISTING ENVIRONMENT PLACEHOLDERS

From `.env.example:49-52`:

```bash
# --- Microsoft Graph OAuth (Milestone 1+) ---
# MICROSOFT_CLIENT_ID=
# MICROSOFT_CLIENT_SECRET=
# MICROSOFT_TENANT_ID=consumers
```

### 24.3 Approved New Settings

**Design-approved.** Exact values are environment-specific configuration.

| Setting | Type | Purpose | Secret |
| :--- | :--- | :--- | :--- |
| `entra_client_id` | str | Entra ID application (client) ID | No |
| `entra_client_secret` | str | Entra ID client secret | **Yes** |
| `entra_tenant_id` | str | Entra ID tenant ID (e.g., `consumers`, `organizations`, or GUID) | No |
| `entra_redirect_uri` | str | Callback URL registered with Entra ID | No |
| `entra_scopes` | list[str] | OAuth scopes requested | No |
| `entra_jwks_endpoint` | str | JWKS discovery endpoint | No |
| `entra_issuer` | str | Expected ID token issuer | No |
| `entra_audience` | str | Expected ID token audience (same as `client_id`) | No |
| `entra_clock_skew_seconds` | int | Bounded clock skew for provider token validation | No |
| `encryption_dek` | str | Data encryption key for provider tokens | **Yes** |

**Secrets management:**
- `entra_client_secret` and `encryption_dek` must be loaded from environment variables or a secrets manager.
- No secrets may be hardcoded or logged.
- Production must reject development defaults (following existing `jwt_signing_secret` pattern).

---

## 25. Testing Strategy

### 25.1 Unit Tests

| Test Area | Focus |
| :--- | :--- |
| State/nonce generation | Cryptographic randomness, length, storage |
| State validation | Mismatch, expiry, missing state |
| Nonce validation | Mismatch, missing nonce, replay |
| PKCE | Code challenge generation, verifier validation |
| ID token validation | Signature, issuer, audience, claims, expiry |
| JWKS cache | Key fetch, cache hit, rotation, 404 refresh |
| Identity resolution | Existing identity, missing identity, duplicate identity, inactive user |
| Tenant resolution | Domain match, Entra tenant match, ambiguous, missing |
| JIT provisioning | User creation, membership creation, rollback on failure |
| Security events | Event emission, request_id propagation, no token leakage |
| Encryption service | Encrypt/decrypt round-trip, wrong key, tampered ciphertext |

### 25.2 Integration Tests

| Test Area | Focus |
| :--- | :--- |
| Authorization flow | Full `/auth/entra` -> callback -> session creation |
| Callback validation | Valid callback, invalid state, invalid nonce, invalid code |
| Identity linking | Link to existing user, reject duplicate, reject cross-tenant |
| JIT provisioning | Create user, assign tenant, create membership, rollback on failure |
| Provider credential storage | Encrypted storage, decryption, refresh, revocation |
| Platform session creation | DeviceSession created, JWT issued, refresh token issued |
| Security events | All provider auth outcomes emit correct events |
| Token isolation | Provider tokens never appear in JWTs, logs, or responses |
| Provider credential refresh | Refresh succeeds, refresh fails (invalid_grant), refresh fails (revoked) |
| Platform session isolation | Provider credential failure does not revoke DeviceSession |

### 25.3 Security Tests

| Test Area | Focus |
| :--- | :--- |
| Token leakage | Provider tokens absent from all outputs |
| CSRF | State validation enforced |
| Replay | Nonce validation enforced |
| Code interception | PKCE enforced |
| Cross-tenant | Entra identity cannot access wrong platform tenant |
| Identity spoofing | Cannot link identity to different User/Tenant |
| JWT claim freeze | No new claims added to platform JWTs |
| Fail-closed | All validation failures reject authentication |
| Request ID | All events contain request_id |

---

---

## 26. Architectural Decisions

### AD-PR13-001: Identity Provider Abstraction

**Decision:**
Identity authentication providers use a dedicated abstraction separate from mail-provider authentication.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

A new `IdentityProviderAuth` Protocol must be created in `packages/providers/src/mip_providers/identity/`. It is distinct from `MailAuthProvider` and is not interchangeable with it.

---

### AD-PR13-002: Microsoft Entra Authorization Flow

**Decision:**
PR-1.3 uses Authorization Code + PKCE with OIDC ID-token validation.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

---

### AD-PR13-003: OAuth CSRF and Replay Protection

**Decision:**
State, nonce, and PKCE are all required.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

---

### AD-PR13-004: Entra Tenant to Platform Tenant Resolution

**Decision:**
The Entra tenant ID is the primary identity-provider tenant identifier.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Rules:**
- Entra tenant ID must map to exactly one platform Tenant.
- Unknown Entra tenant → authentication failure.
- Inactive platform Tenant → authentication failure.
- Ambiguous mapping → authentication failure.
- Email domain MUST NOT independently authorize tenant membership.
- The callback must never allow the client to supply `tenant_id` or `organization_id`.
- `organization_id` is resolved server-side from the platform Tenant.
- Cross-tenant identity linking is prohibited.

If the repository currently lacks the mapping needed to implement this, the required schema/configuration change must be documented as an implementation prerequisite rather than pretending it exists.

---

### AD-PR13-005: Just-in-Time Provisioning

**Decision:**
JIT provisioning is approved for PR-1.3, subject to the constraints below.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Rules:**
- JIT may create a User only after successful provider authentication.
- Tenant must already exist and be active.
- Organization must already be resolvable from the platform Tenant.
- Email alone MUST NOT be sufficient to link an existing account.
- Existing `Identity(provider="microsoft", provider_user_id=...)` is authoritative.
- Duplicate identity → fail closed.
- Identity already belonging to another User → fail closed.
- JIT must not create arbitrary Tenants or Organizations.
- User creation and Identity creation must occur atomically.
- Membership creation must follow the platform's existing tenant/membership model.
- No cross-tenant automatic linking.

**Implementation prerequisites:**
If the existing repository does not contain sufficient membership/provisioning primitives, they must be implemented before JIT can function.

---

### AD-PR13-006: OAuth State Storage

**Decision:**
Server-side database-backed state store.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Requirements:**
- Dedicated OAuth authentication-state record/model if needed.
- Contains only the minimum state necessary:
  - state identifier/hash
  - provider
  - redirect context
  - PKCE verifier or securely associated PKCE material
  - nonce
  - creation/expiry time
  - request_id if useful for correlation
  - consumed timestamp
- Short expiration.
- Single use.
- Atomic consumption.
- Invalid/expired/consumed state → fail closed.
- State values never logged.
- PKCE verifier never exposed to the client.
- State cannot encode trusted tenant/user authorization data.

Do NOT use Redis, client-controlled state, unsigned state, or long-lived state JWTs.

---

### AD-PR13-007: Identity Provider Credential Storage

**Decision:**
New `IdentityProviderCredential` model. Separate from mail-scoped `ProviderCredential`.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

Repository-level tenant isolation must be enforced for all queries against this table.

---

### AD-PR13-008: Provider Credential Encryption

**Decision:**
Full envelope encryption.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**CURRENT STATE:**
No complete provider credential encryption service currently exists. The repository claims OAuth tokens are encrypted at rest (`SECURITY.md:14`), but no encryption/decryption implementation was found.

**PR-1.3 REQUIREMENT:**
An encryption service must be implemented before provider credentials are persisted.

**Requirements:**
- OAuth provider credentials encrypted at rest.
- DEK encrypts credential payload.
- KEK/master key protects DEK.
- `encryption_key_id` identifies the active key material.
- AES-256-GCM authenticated encryption.
- Plaintext provider tokens never persisted.
- Plaintext provider tokens never logged.
- Plaintext provider tokens never included in SecurityEvent metadata.
- Decryption failure fails closed.
- Key rotation must be possible without exposing plaintext outside the credential service boundary.

---

### AD-PR13-009: Microsoft Entra ID Token Validation

**Decision:**
Provider-specific JWKS validation. Platform TokenService is not modified.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Requirements:**
- signature validated against Entra ID JWKS
- allowed asymmetric algorithm: RS256 only
- issuer validated
- audience validated
- expiration validated
- not-before (`nbf`) validated
- issued-at (`iat`) reasonableness validated (reject tokens materially in the future beyond configured clock skew)
- nonce validated
- tenant/issuer consistency validated
- key ID (`kid`) validated
- JWKS key rotation handled
- Clock skew: bounded, explicitly configured for provider token validation (platform `jwt_clock_skew_seconds` does not apply)
- Do not claim this infrastructure exists today

---

### AD-PR13-010: AuthenticationContext Provider Persistence

**Decision:**
`provider` is derived server-side from the authenticated identity, never client-supplied, and never embedded in the platform JWT.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Mechanism:**
Platform JWT claims are frozen and do NOT contain provider information. On subsequent authenticated requests, the middleware must derive `provider` from the server-side identity/session relationship, e.g.:

platform JWT → DeviceSession → associated authenticated platform identity → provider

If `DeviceSession` currently lacks the required association, the minimum schema/model relationship required must be identified and implemented.

`authentication_method` remains `"session"` unless there is a repository-backed reason to change it.

The callback handler setting `provider` on the initial request is NOT sufficient for subsequent requests.

---

### AD-PR13-011: Callback Token Exposure Prevention

**Decision:**
Platform access/refresh tokens must never be exposed in URLs, query parameters, path parameters, redirect URLs, logs, referrers, or security events.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

`POST /auth/callback/entra` must return a server-generated authentication response directly. Provider tokens must never be placed in redirect URLs. If a frontend/browser handoff is required later, it must be deferred and designed separately.

---

### AD-PR13-012: Provider Credential Refresh Isolation

**Decision:**
Entra refresh token refreshes Entra credentials only. It never rotates the platform DeviceSession refresh token. Background refresh is NOT required for initial PR-1.3.

**Status:**
APPROVED FOR PR-1.3 DESIGN.

**Rules:**
- Provider credential failure does not revoke platform `DeviceSession`.
- Refresh logic belongs to the provider credential service/adapter.
- On-demand refresh may be specified.
- Background refresh can be explicitly deferred.
- Provider refresh failure must emit a security/operational event, preserve the platform session, mark provider credential state appropriately, and allow later reauthorization.
- User-facing notification for refresh failure can be deferred.

---

## 27. Acceptance Criteria

### 27.1 Functional Acceptance

- A valid Entra ID authorization flow establishes a platform `DeviceSession`.
- Invalid `state` cannot establish a platform session and emits `CALLBACK_FAILED`.
- Reused or expired `state` cannot establish a platform session and emits `CALLBACK_FAILED`.
- Invalid `nonce` cannot establish a platform session and emits `CALLBACK_FAILED`.
- Invalid ID token signature cannot establish a platform session and emits `CALLBACK_FAILED`.
- Invalid issuer cannot establish a platform session and emits `CALLBACK_FAILED`.
- Invalid audience cannot establish a platform session and emits `CALLBACK_FAILED`.
- ID token with invalid/expired/too-far-future `nbf` or `iat` fails closed and emits `CALLBACK_FAILED`.
- A valid authorization code without correct PKCE verifier is rejected.
- An Entra identity linked to an inactive platform User is rejected.
- An Entra identity linked to a different platform User is rejected.
- Cross-tenant identity linking fails closed.
- Unknown Entra tenant cannot establish a platform session.
- Duplicate identity linking fails closed.
- JIT provisioning creates User, Identity, and Membership in a single transaction.
- JIT provisioning rollback occurs if tenant resolution or membership creation fails.
- Provider tokens are encrypted at rest and decrypted correctly on read.
- Provider credential refresh updates stored credentials without affecting platform session.
- Provider credential refresh failure does not revoke the platform `DeviceSession`.
- Every provider authentication outcome emits the appropriate security event with `request_id`.
- `AuthenticationContext.provider` is `"microsoft"` for Entra-authenticated requests on subsequent authenticated requests.
- Provider tokens never appear in platform JWTs.
- Provider tokens never appear in URLs, query parameters, redirect responses, logs, or security events.

### 27.2 Security Acceptance

- Provider tokens never appear in platform JWTs.
- Provider tokens never appear in security events.
- Provider tokens never appear in API responses.
- Provider tokens never appear in application logs.
- `AuthenticationContext.provider` is `"microsoft"` for Entra-authenticated requests.
- `AuthenticationContext.authentication_method` remains `"session"`.
- Platform JWTs contain only the frozen claim set: `sub`, `tid`, `oid`, `sid`, `jti`, `iss`, `aud`, `exp`, `iat`, `nbf`.
- All public routes are explicitly declared; callback endpoint is public.
- All protected routes continue to require authentication via existing `AuthenticationMiddleware`.
- Tenant isolation is enforced at the repository layer for all new queries.

### 27.3 Non-Functional Acceptance

- All new code passes `ruff check` and `ruff format --check`.
- All new unit tests pass.
- All new integration tests pass (when PostgreSQL test environment is available).
- No existing tests are broken.
- `AuthenticationService`, `SessionService`, and `TokenService` remain unchanged (unless explicitly approved).
- Platform JWT claim contract remains unchanged.
- Existing `DeviceSession` architecture remains stable.

---

## 28. Open Decisions Requiring Approval

The following decisions must be resolved and approved before PR-1.3 implementation begins:

1. **Release version:** v0.3.0-alpha.5 or v0.4.0-alpha.1? (Section 2) — LOW IMPACT

All other previously open decisions have been resolved by the approved architectural decisions in this EDD.

Note: JWKS library selection, exact redirect URI registration, and exact Entra ID scope values are environment-specific configuration, not design decisions, and do not block EDD approval.

---

## 29. Out of Scope (Reconfirmed)

The following remain explicitly out of scope for PR-1.3 and must not be introduced during implementation:

- MFA / TOTP
- Frontend authentication UI
- Service-to-service authentication
- Rate limiting / brute-force protection
- Mail sync/search/AI security
- Production hardening
- Other identity providers (Google, Okta, SAML)
- Platform JWT claim expansion
- Password authentication
- Platform session isolation changes
- Identity unlinking
- Capability discovery endpoint (`GET /auth/capabilities`)

---

## 30. Dependencies

### 30.1 Internal Dependencies

| Component | Status | Purpose |
| :--- | :--- | :--- |
| `AuthenticationService` | Implemented (PR-1.2.5) | Platform session and token creation |
| `DeviceSession` | Implemented (PR-1.2.3) | Stable platform session identity |
| `RefreshTokenFamily` | Implemented (PR-1.2.3) | Platform token epoch tracking |
| `AuthenticationMiddleware` | Implemented (PR-1.2.4) | HTTP-boundary authentication enforcement |
| `AuthenticationContext` | Implemented (PR-1.2.4) | Request-scoped identity/authorization |
| `PolicyEngine` | Implemented (PR-1.2.4) | Authorization decisions |
| `PublicRoutes` | Implemented (PR-1.2.4) | Public route allow-list |
| `SecurityEventEmitter` | Implemented (PR-1.2.1) | Security event emission |
| `Identity` model | Implemented (PR-1.2.1) | External identity bindings |
| `Settings` | Implemented (PR-1.2.1) | Configuration management |

### 30.2 External Dependencies

| Dependency | Purpose | Status |
| :--- | :--- | :--- |
| Microsoft Entra ID tenant | Identity provider | Requires external tenant configuration |
| JWKS endpoint | Token validation | Requires Entra tenant configuration |

---

## 31. Risk Register

| Risk | Likelihood | Impact | Mitigation | Status |
| :--- | :--- | :--- | :--- | :--- |
| No encryption service exists | High | High | Implement `EncryptionService` as part of PR-1.3 (AD-PR13-008) | Open |
| No JWKS infrastructure exists | High | High | Implement JWKS validation per AD-PR13-009 | Open |
| State/nonce storage unavailable | Low | High | Database-backed state store per AD-PR13-006 | Resolved |
| Provider credential refresh deferred | Medium | Medium | Platform session isolation preserves functionality per AD-PR13-012 | Resolved |
| Version naming inconsistency | Low | Low | Requires documentation update (out of scope for this EDD) | Open |

---

*This document is a draft. No implementation has begun. No source code has been modified. No database migrations have been created. No tests have been written. AD-PR13-001 through AD-PR13-012 are APPROVED FOR PR-1.3 DESIGN. The release-version decision remains LOW IMPACT and OPEN, but does not block implementation readiness. Implementation requires explicit approval.*

---

## Document Control

| Version | Date | Author | Changes |
| :--- | :--- | :--- |
| 0.1.0 | 2026-08-15 | Kilo | Initial draft created from repository archaeology |
| 0.2.0 | 2026-08-16 | Kilo | Revised after approval audit: applied AD-PR13-001 through AD-PR13-012, updated threat model, acceptance criteria, API contracts, and resolved obsolete open decisions |
| 0.2.1 | 2026-08-16 | Kilo | Reconciled contradictions: callback JSON architecture, database-backed state store, tenant resolution status, database prerequisites, and document control |

---

EDD REVISION COMPLETE — AWAITING FINAL APPROVAL


