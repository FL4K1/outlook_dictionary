# Development Guide

## Coding Standards

### Python

- **Formatter**: Ruff (runs in CI, enforced via pre-commit)
- **Linter**: Ruff with strict rule set (see `ruff.toml`)
- **Type Checker**: MyPy in strict mode
- **Python Version**: 3.12+ required
- **Line Length**: 100 characters

### Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(search): add semantic search with kNN retrieval
fix(sync): handle Graph API 429 with exponential backoff
docs(api): update search endpoint documentation
refactor(auth): extract token encryption into service
test(search): add golden dataset benchmark
chore(deps): upgrade FastAPI to 0.115.0
```

### Branch Strategy

Trunk-based development with short-lived feature branches:

- `main` — always deployable, protected
- `feat/<ticket>-<description>` — feature branches
- `fix/<ticket>-<description>` — bugfix branches

## Adding a New API Module

1. Create a directory under `apps/api/app/`:
   ```
   apps/api/app/your_module/
   ├── __init__.py
   ├── router.py      # FastAPI router with endpoints
   ├── schemas.py      # Pydantic request/response models
   ├── service.py      # Business logic
   └── dependencies.py # Module-specific DI
   ```

2. Register the router in `apps/api/app/main.py`:
   ```python
   from app.your_module.router import router as your_router
   app.include_router(your_router, prefix="/api/v1")
   ```

3. Add tests in `apps/api/tests/test_your_module.py`

## Adding a New Shared Package

1. Create under `packages/`:
   ```
   packages/your_package/
   ├── pyproject.toml
   └── src/
       └── mip_your_package/
           ├── __init__.py
           └── ...
   ```

2. Add the package name to `ruff.toml` → `known-first-party`

3. Install in development: `pip install -e packages/your_package`

## Database Migrations

```bash
# Create a new migration
make migrate-new msg="add users table"

# Apply migrations
make migrate

# Rollback last migration
make migrate-down
```

**Rules:**
- Every migration must be reversible (implement `downgrade()`)
- Never modify a migration that has been applied in staging/production
- Test migrations against a copy of production data before deploying

## Testing

```bash
make test          # Run all tests
make test-cov      # Run with coverage report
```

**Test structure:**
- Unit tests: Test individual functions in isolation
- Integration tests: Test API endpoints with real dependencies (via testcontainers)
- Tests live alongside the app they test: `apps/api/tests/`, `packages/models/tests/`

## Pre-commit Hooks

Install hooks locally:
```bash
pip install pre-commit
pre-commit install
```

Hooks run automatically on every commit:
- Trailing whitespace removal
- YAML/TOML/JSON validation
- Ruff linting and formatting
- Secret detection (gitleaks)

## Error Handling

All exceptions should extend `AppError` from `app.common.exceptions`:

```python
from app.common.exceptions import AppError

class MailboxNotFoundError(AppError):
    status_code = 404
    error_code = "MAILBOX_NOT_FOUND"
    message = "The specified mailbox was not found."
```

This automatically produces a consistent JSON error response.

## Logging

Use structlog — never use `print()`:

```python
from app.common.logging import get_logger

logger = get_logger(__name__)
logger.info("sync_completed", mailbox_id="abc", email_count=42)
```

**Never log**: email content, subject lines, sender addresses, OAuth tokens, or any PII.
