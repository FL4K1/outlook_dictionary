# Technical Debt Register

> Tracks intentional technical limitations and architectural debt items that require future resolution.

---

| ID | Item | Impact | Resolution | Status |
|:---|:---|:---|:---|:---|
| 1 | Session rows represented refresh epochs (pre-PR-1.2.3) | Session identity changed on every refresh, complicating middleware, audit, and device management | Resolved in PR-1.2.3 by introducing stable `DeviceSession` model with rotating `RefreshTokenFamily` epochs. Session identity is now decoupled from token rotation. | Closed |
| 2 | SR-053–SR-060 source text unavailable | Full EDD compliance cannot be claimed; 9 security requirements (SR-053 through SR-060) cannot be verified against implementation | Obtain complete EDD with SR-053–SR-060 and perform traceability review. | Open |
| 3 | PostgreSQL-backed integration tests deferred | Database-dependent validation of DeviceSession revocation, tenant isolation, membership resolution, and role/permission loading is unverified against real DB | Complete integration tests when PostgreSQL testcontainers are available in CI or local environment. | Open |
| 4 | Security test coverage gaps | 11 security-critical paths (revoked/expired sessions, inactive tenant/membership, tenant/org mismatch, permission/role denial, event emission) lack dedicated integration tests; mocked control flow verified but not end-to-end | Add integration tests covering revoked session, expired session, idle session, inactive tenant, missing membership, tenant mismatch, organization mismatch, permission denial, role denial, authorization events, middleware ordering, and context isolation. | Open |

---

*This register is maintained per ENGINEERING_RULES.md.*
