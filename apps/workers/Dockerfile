FROM python:3.12-slim AS base

WORKDIR /opt/app

# curl is required by the container HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Shared domain packages (installed first for layer caching) ---
COPY packages/ /opt/packages/
RUN pip install --no-cache-dir \
    /opt/packages/models \
    /opt/packages/providers \
    /opt/packages/ai \
    /opt/packages/email_parser

# --- API application ---
# PR-3.3 / B1: the worker legitimately consumes the API package. The outbox
# projection reuses app.repositories.mail, the sync job reuses the frozen
# SyncOrchestrator (app.services.sync_orchestrator), and worker startup reads the
# canonical Settings plus the canonical logging setup from app.common.*.
# Previously the worker imported all of these while the image installed none of
# them, so `python -m mip_workers` raised ModuleNotFoundError before ARQ started.
COPY apps/api/pyproject.toml /opt/api/
COPY apps/api/app/ /opt/api/app/
RUN pip install --no-cache-dir /opt/api

# --- Alembic environment (migration step) ---
# Migrations are run by the `migrate` compose service, which shares this image.
# `mip_workers.migrations` drives Alembic through its Python API and supplies
# sqlalchemy.url from DATABASE_URL, because apps/api/alembic/env.py resolves the
# URL exclusively from the Alembic config rather than from application Settings.
COPY apps/api/alembic.ini /opt/migrate/alembic.ini
COPY apps/api/alembic/ /opt/migrate/alembic/
ENV MIP_ALEMBIC_DIR=/opt/migrate/alembic
RUN pip install --no-cache-dir "alembic>=1.13.0"

# --- Worker application ---
# Note: src/ must be copied before `pip install .` because hatchling declares
# `packages = ["src/mip_workers"]` and fails to build when the package directory
# is absent. The previous image installed from pyproject.toml alone.
COPY apps/workers/pyproject.toml /opt/app/
COPY apps/workers/src/ /opt/app/src/
RUN pip install --no-cache-dir .

# --- Run as a non-root user ---
RUN useradd --create-home --uid 10001 mip \
    && chown -R mip:mip /opt
USER mip

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The worker has no HTTP listener. Liveness is asserted against the ARQ
# heartbeat key in Redis, which WorkerSettings writes on health_check_interval.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "from mip_workers.worker import check_health; check_health()"]

CMD ["python", "-m", "mip_workers"]
