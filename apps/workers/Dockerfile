FROM python:3.12-slim AS base

WORKDIR /opt/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ /opt/packages/
RUN pip install --no-cache-dir \
    /opt/packages/models \
    /opt/packages/providers \
    /opt/packages/email_parser

COPY apps/workers/pyproject.toml /opt/app/
RUN pip install --no-cache-dir .

COPY apps/workers/src/ /opt/app/src/

CMD ["python", "-m", "mip_workers"]
