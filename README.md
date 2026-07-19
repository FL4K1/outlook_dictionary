# Mail Intelligence Platform

Enterprise mail intelligence with multi-stage search and AI reasoning.

[![CI](https://github.com/FL4K1/outlook_dictionary/actions/workflows/ci.yml/badge.svg)](https://github.com/FL4K1/outlook_dictionary/actions/workflows/ci.yml)

## Overview

A production-grade, multi-tenant SaaS platform that enables organizations to securely connect mail accounts (starting with Microsoft 365), continuously synchronize mail, and retrieve information through a multi-stage search pipeline combining deterministic retrieval with AI-assisted reasoning.

**Architecture principle:** AI enhances retrieval. AI never replaces retrieval.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI (Python 3.12+) |
| Database | PostgreSQL 16 |
| Search | Elasticsearch 8.x |
| Cache / Queue | Redis 7 |
| Object Storage | S3 / MinIO |
| Frontend | Next.js 14+ (TypeScript) |
| AI | Azure OpenAI / OpenAI |

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- Node.js 20+ (for frontend)
- Git

### Setup

```bash
# Clone the repository
git clone https://github.com/FL4K1/outlook_dictionary.git
cd outlook_dictionary

# Create virtual environment and install all packages
make setup

# Start infrastructure services (Postgres, Redis, ES, MinIO)
make docker-up

# Run database migrations
make migrate

# Start the development server
make dev
```

The API will be available at http://localhost:8000.

API documentation is at http://localhost:8000/docs.

### Common Commands

```bash
make help          # Show all available commands
make dev           # Start FastAPI dev server with hot reload
make test          # Run all tests
make lint          # Run ruff linter
make fmt           # Auto-format code
make typecheck     # Run mypy type checker
make docker-up     # Start infrastructure services
make docker-down   # Stop infrastructure services
make docker-reset  # Stop services and remove all data
make migrate       # Run database migrations
make clean         # Remove build artifacts and caches
```

## Project Structure

```
├── apps/
│   ├── api/             # FastAPI backend application
│   ├── workers/         # Background task workers (sync, index, embed)
│   ├── webhook/         # Lightweight webhook receiver
│   └── web/             # Next.js frontend
├── packages/
│   ├── models/          # SQLAlchemy ORM models and Pydantic schemas
│   ├── providers/       # Mail provider abstractions (Graph, Gmail, IMAP)
│   ├── ai/              # LLM and embedding provider interfaces
│   └── email_parser/    # MIME parsing and text extraction
├── infra/
│   └── docker/          # Docker Compose for local development
├── docs/                # Documentation
└── .github/             # CI/CD workflows
```

## Development

See [docs/getting-started.md](docs/getting-started.md) for detailed setup instructions.

See [docs/development-guide.md](docs/development-guide.md) for coding standards and workflows.

## Architecture

The full architecture is documented in the Engineering Blueprint. Key design decisions:

- **Provider Abstraction**: Mail, LLM, and embedding providers are all behind Protocol interfaces
- **8-Layer Data Flow**: Provider → Sync → Normalize → Store → Index → Retrieve → Reason → Present
- **Search Explainability**: Every search result explains why it matched
- **Pipeline Versioning**: Every search result records its pipeline version

## License

Proprietary. All rights reserved.
