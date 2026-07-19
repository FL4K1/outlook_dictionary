# Getting Started

This guide walks you through setting up the Mail Intelligence Platform for local development.

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Backend runtime |
| Docker | 24+ | Infrastructure services |
| Docker Compose | v2+ | Multi-container orchestration |
| Node.js | 20+ | Frontend development |
| Git | 2.40+ | Version control |

## Step 1: Clone and Setup

```bash
git clone https://github.com/FL4K1/outlook_dictionary.git
cd outlook_dictionary
```

## Step 2: Create Environment

```bash
# This creates a virtual environment, installs all packages, and copies .env.example to .env
make setup

# Activate the virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
```

## Step 3: Start Infrastructure

```bash
# Start PostgreSQL, Redis, Elasticsearch, and MinIO
make docker-up

# Verify all services are running
docker ps
```

Expected output — 4 containers running:
- `mip-postgres` (port 5432)
- `mip-redis` (port 6379)
- `mip-elasticsearch` (port 9200)
- `mip-minio` (ports 9000, 9001)

## Step 4: Run Database Migrations

```bash
make migrate
```

This creates the initial `tenant` table in PostgreSQL.

## Step 5: Start the API Server

```bash
make dev
```

The server starts at http://localhost:8000 with hot reload enabled.

### Verify the Setup

```bash
# Liveness check (always returns healthy)
curl http://localhost:8000/health/live

# Readiness check (verifies database connectivity)
curl http://localhost:8000/health/ready

# OpenAPI documentation
# Open in browser: http://localhost:8000/docs
```

## Step 6: Run Tests

```bash
make test
```

## Step 7: Verify Code Quality

```bash
make lint        # Check for lint errors
make fmt         # Auto-fix formatting
make typecheck   # Run type checker
```

## Environment Variables

All configuration is loaded from environment variables. See `.env.example` for the complete list.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Environment: development, testing, staging, production |
| `APP_DEBUG` | `true` | Enable debug mode and SQL echo |
| `APP_LOG_LEVEL` | `DEBUG` | Python log level |
| `APP_LOG_FORMAT` | `console` | Log format: console (dev) or json (prod) |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `mip` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `mip_dev_password` | PostgreSQL password |
| `POSTGRES_DB` | `mail_intelligence` | PostgreSQL database name |

## Troubleshooting

### Docker services won't start

```bash
# Check if ports are already in use
netstat -an | findstr "5432 6379 9200 9000"

# Reset everything
make docker-reset
make docker-up
```

### Database migration fails

Ensure PostgreSQL is running and accepting connections:
```bash
docker exec mip-postgres pg_isready -U mip
```

### Tests fail with import errors

Ensure all packages are installed in editable mode:
```bash
make install
```
