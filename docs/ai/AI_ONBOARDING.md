# AI Engineering Onboarding

Read this FIRST before any implementation task.

---

## Project Vision & Product Philosophy
The **Mail Intelligence Platform (MIP)** is a production-grade, multi-tenant SaaS platform enabling organizations to securely connect mail accounts, synchronize mail, and retrieve information through a multi-stage search pipeline.
**Product Philosophy:** AI enhances retrieval; it never replaces it. Every answer must trace back to source emails via deterministic retrieval.

## Engineering & AI Philosophy
**Engineering Philosophy:** Architecture is intentionally frozen. No implementation begins without a rigorous design process. We optimize for long-term maintainability, security, and multi-tenant isolation over speed.
**AI Philosophy:** AI agents are amplifiers of the engineering workflow, not replacements for it. AI must obey the repository's strict governance and never drift from the established architecture.

## Repository Workflow & Development Lifecycle
Every implementation follows this exact pipeline:
```
Threat Model → Engineering Design Document (EDD) → Implementation Contract → Implementation → Validation → Documentation Update → Merge → Release
```
No AI agent should ever bypass this workflow.

## Security Mindset
- **Zero Trust:** Fail closed. Never trust client claims.
- **Tokens:** JWTs contain ONLY identity/session claims (e.g., `sub`, `tid`, `oid`). Roles and permissions are NEVER in JWTs.
- **Secrets:** Never hardcode secrets. Never log tokens, email content, subject lines, or PII.

## Review & Merge Process
- No merge occurs without passing validation gates: `ruff check`, `ruff format`, `mypy`, and 100% test pass rate.
- Every merge must include corresponding documentation and CHANGELOG updates.

## Required Context Loading Order
Before writing a single line of code, load context in this exact order:
1. `AI_ONBOARDING.md`
2. `PROJECT_STATE.md`
3. `ENGINEERING_RULES.md`
4. `CURRENT_SPRINT.md`
5. Threat Model (if applicable)
6. Relevant Engineering Design Document
7. Implementation Contract
8. Relevant Source Code

## Checklist Before Implementation
- [ ] Context loaded in the exact required order.
- [ ] Understand existing code boundaries and IN/OUT of scope definitions.
- [ ] Technical Debt Register checked for constraints.

## Checklist Before Merge
- [ ] Validation gates passed (tests, Ruff, MyPy).
- [ ] CHANGELOG and Documentation updated.
- [ ] No scope creep beyond the approved Implementation Contract.
