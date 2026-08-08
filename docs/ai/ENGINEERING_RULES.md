# Engineering Rules

> The Constitution. Every AI implementation must obey these rules.

---

## AI Behaviour Rules

1. **No AI agent may redesign architecture during implementation.**
2. Always read `docs/ai/AI_ONBOARDING.md` before any implementation.
3. Always read `docs/ai/CURRENT_SPRINT.md` to understand strict scope boundaries.
4. Never expand scope beyond the approved Engineering Design Document (EDD).
5. Never refactor code outside the current sprint's scope without approval.

## Architecture Rules

1. Never redesign frozen architecture or ADRs without explicit owner approval.
2. Never bypass the repository layer. All database access goes through repositories.
3. Never bypass the service layer. Business logic lives in services, not routers.
4. Never allow provider-specific code in core platform services.
5. Always enforce tenant isolation at the repository layer. Cross-tenant access is forbidden.
6. Always use Protocol interfaces (PEP 544) for abstraction boundaries.

## Security Rules

1. Never put roles, permissions, mailbox IDs, or provider tokens into JWTs.
2. Never log tokens, secrets, email content, subject lines, sender addresses, or PII.
3. Never hardcode secrets. All secrets come from environment variables.
4. Always hash refresh tokens with SHA-256 before storage.
5. Always fail closed. If a security check cannot be performed, deny access.

## Coding Rules

1. Python 3.12+ is required.
2. Never use `print()`. Use `structlog`.
3. Always extend `AppError` for domain exceptions.
4. Line length is 100 characters.
5. Every Alembic migration must implement `downgrade()`.

## Testing Rules

1. Every new module must have corresponding unit tests.
2. Every bug fix must include a regression test.
3. Never remove or weaken existing tests without justification.
4. Coverage threshold is 85%.

## Documentation Rules

1. Always update `CHANGELOG.md` for every release.
2. Always update the Technical Debt Register when introducing intentional limitations.
3. Always update ADR Traceability when implementing or deferring ADR scope.

## Git & Review Rules

1. Always use annotated tags for releases.
2. Always use `--no-ff` merges to main to preserve branch history.
3. Every merge requires passing all quality gates: Ruff check, Ruff format, MyPy, and tests.
4. Every implementation slice requires an approved EDD.
