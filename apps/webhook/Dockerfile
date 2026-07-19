FROM python:3.12-slim AS base

WORKDIR /opt/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ /opt/packages/
RUN pip install --no-cache-dir /opt/packages/providers

COPY apps/webhook/pyproject.toml /opt/app/
RUN pip install --no-cache-dir .

COPY apps/webhook/src/ /opt/app/src/

EXPOSE 8001

CMD ["uvicorn", "mip_webhook:app", "--host", "0.0.0.0", "--port", "8001"]
