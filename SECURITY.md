# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Email security concerns to the project maintainers directly.

## Security Practices

- All secrets are loaded from environment variables, never hardcoded.
- OAuth tokens are encrypted at rest using envelope encryption.
- All API endpoints require authentication (except health checks).
- Tenant data isolation is enforced at every data access layer.
- Dependencies are scanned for vulnerabilities in CI/CD.
- Pre-commit hooks include secret detection (gitleaks).

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x.x   | ✅ Current development |
