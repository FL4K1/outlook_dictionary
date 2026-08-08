# Engineering Decision Log

> This log records important engineering decisions that shape the platform but do not warrant a full Architecture Decision Record (ADR). Kept concise and chronological.

---

### Date: 2026-08-01
**Decision**: Provider authentication is strictly isolated from platform authentication.
**Reason**: Mail intelligence relies heavily on background synchronization. If provider credentials (e.g., Microsoft Graph tokens) expire or are revoked, the user's platform session must remain valid so they can be prompted to re-authorize, rather than being abruptly logged out.
**Impact**: Requires separate OAuth flows and independent credential storage (PR-1.3).
**Related ADR**: ADR-005
**Current Status**: Active

---

### Date: 2026-08-02
**Decision**: JWTs never contain roles or permissions.
**Reason**: Security and freshness. If an administrator revokes a user's permission, a JWT containing that permission would remain valid until expiration. By resolving authorization server-side, revocation is instantaneous.
**Impact**: Middleware must perform a database lookup or cache query to construct the `AuthenticationContext` on every request.
**Related ADR**: ADR-008
**Current Status**: Active

---

### Date: 2026-08-03
**Decision**: Deterministic retrieval always precedes AI reasoning.
**Reason**: LLMs are prone to hallucination and cannot scale to search a million-email inbox efficiently. Deterministic engines (Elasticsearch) must filter the corpus to a small, highly relevant candidate set before AI is applied.
**Impact**: The search pipeline is multi-staged. AI is strictly the final step.
**Related ADR**: N/A
**Current Status**: Active

---

### Date: 2026-08-04
**Decision**: AI never searches the entire mailbox.
**Reason**: Context window limitations and cost. The platform relies on Elasticsearch vector and keyword search to retrieve the top-K candidates.
**Impact**: Embeddings must be generated for all incoming mail during the sync pipeline (PR-1.6).
**Related ADR**: N/A
**Current Status**: Active

---

### Date: 2026-08-05
**Decision**: Session Infrastructure (PR-1.2.3) precedes Middleware (PR-1.2.4).
**Reason**: Middleware's primary job is to validate sessions and hydrate the request context. It cannot be built reliably without a stable device-session model and database schema to query against.
**Impact**: Delays API endpoints until PR-1.2.5.
**Related ADR**: ADR-009
**Current Status**: Active

---

### Date: 2026-08-07
**Decision**: Repository architecture is intentionally frozen during implementation phases.
**Reason**: To enable the long-term use of multiple AI coding agents (Claude, Codex, Kilo Code). A frozen architecture prevents AI-driven architectural drift, ensuring agents amplify output rather than diverging the codebase.
**Impact**: AI agents are explicitly forbidden from redesigning architecture.
**Related ADR**: N/A
**Current Status**: Active


---

### Date: 2026-08-08
**Decision**: Architecture Reconciliation — Stable DeviceSession with rotating RefreshTokenFamily epochs.
**Reason**: The Implementation Contract Section 8.3 prescribed revoking and recreating DeviceSession on every refresh, which contradicted the EDD objective of decoupling session identity from token rotation. The contract was corrected to update DeviceSession in-place (current_refresh_token_hash, last_active_at) while rotating RefreshTokenFamily epochs. This resolves the original technical debt where session rows represented refresh epochs.
**Impact**: SessionService.refresh_session() now preserves DeviceSession identity across refreshes. Middleware, audit trails, and future provider integrations can rely on stable session IDs.
**Related ADR**: ADR-009
**Current Status**: Active
