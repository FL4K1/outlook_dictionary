# Technical Debt Register

> Tracks intentional technical limitations and architectural debt items that require future resolution.

---

| ID | Item | Impact | Resolution | Status |
|----|------|--------|------------|--------|
| 1 | Session rows represented refresh epochs (pre-PR-1.2.3) | Session identity changed on every refresh, complicating middleware, audit, and device management | Resolved in PR-1.2.3 by introducing stable `DeviceSession` model with rotating `RefreshTokenFamily` epochs. Session identity is now decoupled from token rotation. | Closed |

---

*This register is maintained per ENGINEERING_RULES.md.*
