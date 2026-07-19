FROM python:3.12-slim AS base

WORKDIR /opt/app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install shared packages first (better layer caching)
COPY packages/ /opt/packages/
RUN pip install --no-cache-dir \
    /opt/packages/models \
    /opt/packages/providers \
    /opt/packages/ai \
    /opt/packages/email_parser

# Install API application
COPY apps/api/pyproject.toml /opt/app/
RUN pip install --no-cache-dir .

COPY apps/api/app/ /opt/app/app/
COPY apps/api/alembic/ /opt/app/alembic/
COPY apps/api/alembic.ini /opt/app/alembic.ini

EXPOSE 8000

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
