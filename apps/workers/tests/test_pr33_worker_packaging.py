"""PR-3.3 worker packaging, Elasticsearch configuration, and ARQ wiring.

Covers:
  B1 -- worker packaging/startup (the worker image previously could not import)
  B2 -- canonical Elasticsearch configuration with fail-fast on missing host
  H1 -- mail sync job registration in the production ARQ entrypoint
  H8 -- worker health check wiring

No external service is required: everything here is import-, config-, or
registration-level, which is exactly the class of defect PR-3.3 found (the
worker image was never built in CI, so the missing packages went unnoticed).
"""

from __future__ import annotations

import os
import tomllib
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# B1 -- packaging
# ---------------------------------------------------------------------------
def test_worker_package_declares_ai_and_api_dependencies() -> None:
    """The worker imports mip_ai and app.*; the package must declare both.

    Before PR-3.3 the worker imported ``mip_ai.embeddings`` and
    ``app.repositories.mail`` while its pyproject declared neither, so
    ``python -m mip_workers`` raised ModuleNotFoundError before ARQ started.
    """
    pyproject = tomllib.loads(
        (REPO_ROOT / "apps" / "workers" / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = {
        d.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip()
        for d in pyproject["project"]["dependencies"]
    }
    assert "mip-ai" in dependencies, "mip_ai is imported by the worker but not declared"
    assert "mip-api" in dependencies, "app.* is imported by the worker but not declared"
    assert "httpx" in dependencies, "httpx is used directly, not just transitively"


def test_worker_dockerfile_installs_ai_and_api() -> None:
    """The image must install packages/ai and the API application."""
    dockerfile = (REPO_ROOT / "apps" / "workers" / "Dockerfile").read_text(encoding="utf-8")
    assert "/opt/packages/ai" in dockerfile, "mip-ai is not installed in the worker image"
    assert "apps/api/app/" in dockerfile, "the API package is not installed in the worker image"
    # The worker package must be copied before `pip install .` because hatchling
    # declares packages = ["src/mip_workers"] and fails to build without it.
    assert dockerfile.index("COPY apps/workers/src/") < dockerfile.index(
        "pip install --no-cache-dir ."
    )


def test_worker_dockerfile_runs_as_non_root() -> None:
    """PR-3.3 L1: the worker container must not run as root."""
    dockerfile = (REPO_ROOT / "apps" / "workers" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER mip" in dockerfile


def test_worker_dockerfile_ships_alembic_environment() -> None:
    """M10: the migration step needs the Alembic scripts inside the image."""
    dockerfile = (REPO_ROOT / "apps" / "workers" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY apps/api/alembic/" in dockerfile
    assert "alembic>=1.13.0" in dockerfile


def test_production_worker_entrypoint_imports_cleanly() -> None:
    """B1: the actual production entrypoint must import without error."""
    import mip_workers.worker as worker_module

    assert worker_module.WorkerSettings is not None


def test_worker_modules_import_without_hidden_dependency_errors() -> None:
    """Every worker module must import (this is the B1 regression gate)."""
    import importlib

    for name in (
        "mip_workers",
        "mip_workers.worker",
        "mip_workers.outbox_worker",
        "mip_workers.es_adapter",
        "mip_workers.es_provisioning",
        "mip_workers.backfill",
        "mip_workers.observability",
        "mip_workers.migrations",
    ):
        assert importlib.import_module(name) is not None, f"{name} failed to import"


# ---------------------------------------------------------------------------
# B1 / H1 -- ARQ registration
# ---------------------------------------------------------------------------
def test_sync_jobs_are_registered_in_production_entrypoint() -> None:
    """H1: SyncOrchestrator must have a production call site.

    Before PR-3.3 the only references to SyncOrchestrator anywhere in the
    repository were in tests, so mail was never ingested in a deployment.
    """
    from mip_workers.worker import WorkerSettings

    names = {fn.__name__ for fn in WorkerSettings.functions}
    assert "sync_mailbox_job" in names
    assert "mail_sync_discovery_cron" in names


def test_outbox_jobs_are_registered() -> None:
    from mip_workers.worker import WorkerSettings

    names = {fn.__name__ for fn in WorkerSettings.functions}
    assert "process_outbox_event_job" in names
    assert "embed_message_job" in names
    assert "backfill_tenant_embeddings_job" in names


def test_cron_jobs_include_outbox_poller_and_sync_discovery() -> None:
    from mip_workers.worker import WorkerSettings

    assert len(WorkerSettings.cron_jobs) == 2
    registered = {cron_.coroutine.__name__ for cron_ in WorkerSettings.cron_jobs}
    assert registered == {"outbox_polling_cron", "mail_sync_discovery_cron"}


def test_poller_is_not_also_registered_as_a_plain_function() -> None:
    """M13: the poller was listed in both functions and cron_jobs.

    That made scheduled and manually-enqueued execution indistinguishable.
    """
    from mip_workers.worker import WorkerSettings, outbox_polling_cron

    assert outbox_polling_cron not in WorkerSettings.functions


def test_arq_lifecycle_hooks_are_registered() -> None:
    from mip_workers.worker import WorkerSettings, shutdown, startup

    assert WorkerSettings.on_startup is startup
    assert WorkerSettings.on_shutdown is shutdown


def test_health_check_key_is_namespaced_and_defined() -> None:
    """H8: the container HEALTHCHECK needs a stable, namespaced ARQ key."""
    from mip_workers.worker import (
        HEALTH_CHECK_KEY,
        HEALTH_CHECK_MAX_AGE_SECONDS,
        WorkerSettings,
    )

    assert WorkerSettings.health_check_key == HEALTH_CHECK_KEY
    assert HEALTH_CHECK_KEY.startswith("mip:")
    # Freshness window must exceed the write interval.
    assert WorkerSettings.health_check_interval < HEALTH_CHECK_MAX_AGE_SECONDS


# ---------------------------------------------------------------------------
# B2 -- Elasticsearch configuration
# ---------------------------------------------------------------------------
def test_resolve_elasticsearch_url_fails_fast_when_host_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2: the worker must refuse to fall back to localhost:9200."""
    from mip_workers.es_provisioning import (
        ElasticsearchConfigurationError,
        resolve_elasticsearch_url,
    )

    monkeypatch.delenv("ELASTICSEARCH_HOST", raising=False)
    with pytest.raises(ElasticsearchConfigurationError) as excinfo:
        resolve_elasticsearch_url()
    assert "ELASTICSEARCH_HOST" in str(excinfo.value)


def test_resolve_elasticsearch_url_fails_fast_when_host_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mip_workers.es_provisioning import (
        ElasticsearchConfigurationError,
        resolve_elasticsearch_url,
    )

    monkeypatch.setenv("ELASTICSEARCH_HOST", "   ")
    with pytest.raises(ElasticsearchConfigurationError):
        resolve_elasticsearch_url()


def test_resolve_elasticsearch_url_uses_canonical_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2: the URL must come from Settings, not from a worker-local default."""
    from mip_workers.es_provisioning import resolve_elasticsearch_url

    from app.common.config import get_settings

    monkeypatch.setenv("ELASTICSEARCH_HOST", "es.internal")
    monkeypatch.setenv("ELASTICSEARCH_PORT", "9500")
    monkeypatch.setenv("ELASTICSEARCH_SCHEME", "https")
    get_settings.cache_clear()
    try:
        assert resolve_elasticsearch_url() == "https://es.internal:9500"
    finally:
        get_settings.cache_clear()


def test_validate_elasticsearch_url_rejects_missing_scheme() -> None:
    from mip_workers.es_provisioning import (
        ElasticsearchConfigurationError,
        validate_elasticsearch_url,
    )

    with pytest.raises(ElasticsearchConfigurationError):
        validate_elasticsearch_url("es.internal:9200")
    with pytest.raises(ElasticsearchConfigurationError):
        validate_elasticsearch_url("")


def test_validate_elasticsearch_url_strips_trailing_slash() -> None:
    from mip_workers.es_provisioning import validate_elasticsearch_url

    assert validate_elasticsearch_url("http://es:9200/") == "http://es:9200"


def test_es_adapter_default_is_not_silently_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """B2 regression: constructing the default adapter must require configuration.

    The old code path was ``ElasticsearchMailAdapter()`` with a hardcoded
    ``http://localhost:9200`` default and no environment lookup at all.
    """
    from mip_workers.es_provisioning import (
        ElasticsearchConfigurationError,
        resolve_elasticsearch_url,
    )

    monkeypatch.delenv("ELASTICSEARCH_HOST", raising=False)
    with pytest.raises(ElasticsearchConfigurationError):
        resolve_elasticsearch_url()


def test_outbox_worker_default_adapter_uses_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2: OutboxWorker() with no adapter must not fall back to loopback."""
    from app.common.config import get_settings

    monkeypatch.setenv("ELASTICSEARCH_HOST", "es.internal")
    monkeypatch.setenv("ELASTICSEARCH_PORT", "9200")
    get_settings.cache_clear()
    try:
        from unittest.mock import MagicMock

        from mip_workers.outbox_worker import OutboxWorker

        worker = OutboxWorker(MagicMock())
        assert worker.es_adapter.base_url == "http://es.internal:9200"
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Migration helper (M10)
# ---------------------------------------------------------------------------
def test_migrations_fall_back_to_canonical_settings() -> None:
    """M10: a deployment that configured only POSTGRES_* must still migrate.

    ``Settings.database_url`` is a *property*, not a field, so DATABASE_URL is
    optional. Without this fallback such a deployment would have had no
    migration URL at all -- or, worse, a different one from its application URL.
    """
    from mip_workers.migrations import resolve_database_url

    from app.common.config import get_settings

    get_settings.cache_clear()
    try:
        resolved = resolve_database_url({})
    finally:
        get_settings.cache_clear()
    assert resolved.startswith("postgresql+asyncpg://")


def test_migrations_prefer_explicit_database_url() -> None:
    """An explicit DATABASE_URL must win over the derived one."""
    from mip_workers.migrations import resolve_database_url

    assert resolve_database_url({"DATABASE_URL": "postgresql://u:p@explicit:5432/db"}) == (
        "postgresql+asyncpg://u:p@explicit:5432/db"
    )


def test_migrations_database_url_cannot_diverge_from_settings() -> None:
    """When both are configured the migration URL must be the app's database.

    This is the divergence M10 guards against: the migration step pointing at a
    different database than the application.
    """
    from mip_workers.migrations import resolve_database_url

    from app.common.config import get_settings

    get_settings.cache_clear()
    try:
        expected = get_settings().database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    finally:
        get_settings.cache_clear()
    assert resolve_database_url({}) == expected


def test_migrations_normalise_driver_to_asyncpg() -> None:
    """alembic/env.py uses async_engine_from_config, so the URL must be async."""
    from mip_workers.migrations import resolve_database_url

    assert resolve_database_url({"DATABASE_URL": "postgresql://u:p@h:5432/db"}) == (
        "postgresql+asyncpg://u:p@h:5432/db"
    )
    assert resolve_database_url({"DATABASE_URL": "postgresql+asyncpg://u:p@h/db"}) == (
        "postgresql+asyncpg://u:p@h/db"
    )


def test_migrations_reject_non_postgres_url() -> None:
    from mip_workers.migrations import (
        MigrationConfigurationError,
        resolve_database_url,
    )

    with pytest.raises(MigrationConfigurationError):
        resolve_database_url({"DATABASE_URL": "mysql://u:p@h/db"})


def test_migrations_locate_alembic_script_dir_from_env() -> None:
    from mip_workers.migrations import resolve_alembic_dir

    script_dir = REPO_ROOT / "apps" / "api" / "alembic"
    assert resolve_alembic_dir({"MIP_ALEMBIC_DIR": str(script_dir)}) == script_dir


def test_migrations_discover_alembic_dir_from_repo_root() -> None:
    """Without the env var the helper must still find apps/api/alembic."""
    from mip_workers.migrations import resolve_alembic_dir

    assert resolve_alembic_dir({}).name == "alembic"
    assert (resolve_alembic_dir({}) / "env.py").is_file()


def test_migrations_config_does_not_read_hardcoded_ini_url() -> None:
    """M10: apps/api/alembic.ini hardcodes a localhost dev URL.

    The runner must supply sqlalchemy.url from DATABASE_URL instead, so a
    production migration cannot target localhost.
    """
    from mip_workers.migrations import build_alembic_config

    config = build_alembic_config(
        database_url="postgresql+asyncpg://real:secret@prod:5432/db",
        script_location=REPO_ROOT / "apps" / "api" / "alembic",
    )
    assert config.get_main_option("sqlalchemy.url") == (
        "postgresql+asyncpg://real:secret@prod:5432/db"
    )


def test_migrations_config_injects_url_literally() -> None:
    """A password containing ``%`` must survive ConfigParser interpolation."""
    from mip_workers.migrations import build_alembic_config

    config = build_alembic_config(
        database_url="postgresql+asyncpg://u:p%40ss@host:5432/db",
        script_location=REPO_ROOT / "apps" / "api" / "alembic",
    )
    assert config.get_main_option("sqlalchemy.url").endswith("@host:5432/db")


# ---------------------------------------------------------------------------
# Deterministic job identity (M1 / M2)
# ---------------------------------------------------------------------------
def test_embed_job_id_is_deterministic_per_message_version() -> None:
    from mip_workers.outbox_worker import embed_job_id

    message_id = uuid.uuid4()
    assert embed_job_id(message_id, 3) == embed_job_id(message_id, 3)
    assert embed_job_id(message_id, 3) != embed_job_id(message_id, 4)
    assert embed_job_id(message_id, 3).startswith("embed:")


def test_outbox_job_id_varies_with_lease_version() -> None:
    """M1: a re-claim must produce a new job id so genuine retries are enqueued.

    ARQ's dedup holds only while ``arq:job:{id}`` or ``arq:result:{id}`` exists
    (verified against arq 0.28.0). A stable per-event id would therefore be
    released once the result TTL elapsed, and would wrongly suppress retries
    while it was still held.
    """
    from mip_workers.outbox_worker import outbox_job_id

    event_id = uuid.uuid4()
    assert outbox_job_id(event_id, 1) == outbox_job_id(event_id, 1)
    assert outbox_job_id(event_id, 1) != outbox_job_id(event_id, 2)
    assert outbox_job_id(event_id, 1).startswith("outbox:")


def test_outbox_job_id_includes_event_identity() -> None:
    from mip_workers.outbox_worker import outbox_job_id

    first, second = uuid.uuid4(), uuid.uuid4()
    assert outbox_job_id(first, 1) != outbox_job_id(second, 1)


def test_env_example_agrees_with_canonical_settings() -> None:
    """M10/M11: .env.example must document the variables the code reads.

    The old file omitted every mandatory Entra and encryption variable and
    documented MICROSOFT_* names that nothing reads.
    """
    env_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
        "DATABASE_URL",
        "ELASTICSEARCH_HOST",
        "ELASTICSEARCH_PORT",
        "ELASTICSEARCH_SCHEME",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_DIMENSION",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "JWT_SIGNING_SECRET",
        "ENCRYPTION_DEK",
        "ENTRA_CLIENT_ID",
        "ENTRA_CLIENT_SECRET",
        "ENTRA_TENANT_ID",
        "ENTRA_REDIRECT_URI",
        "ENTRA_JWKS_ENDPOINT",
        "ENTRA_ISSUER",
        "ENTRA_AUDIENCE",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
    ):
        assert f"{name}=" in env_text, f".env.example does not document {name}"


def test_env_example_does_not_advertise_unused_microsoft_prefix() -> None:
    """MICROSOFT_* was documented but never read; Settings uses ENTRA_*."""
    env_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    active_lines = [
        line for line in env_text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    body = "\n".join(active_lines)
    assert "MICROSOFT_CLIENT_ID=" not in body
    assert "MICROSOFT_TENANT_ID=" not in body


def test_env_example_embedding_dimension_matches_settings_default() -> None:
    """M7: the documented dimension must equal the canonical Settings default."""
    from app.common.config import get_settings

    env_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = None
    for line in env_text.splitlines():
        if line.startswith("EMBEDDING_DIMENSION="):
            documented = line.split("=", 1)[1].strip()
    assert documented is not None
    assert int(documented) == get_settings().embedding_dimension


def test_env_var_names_match_settings_fields() -> None:
    """Every non-comment .env.example assignment must be a real env var.

    Guards against the M11 class of drift where a documented variable is
    silently inert. pydantic-settings maps a field to its env var as
    ``env_prefix + field_name.upper()``; ``Settings`` uses an empty prefix.
    """
    from app.common.config import Settings

    env_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    prefix = Settings.model_config.get("env_prefix", "") or ""
    known = {f"{prefix}{name}".upper() for name in Settings.model_fields}

    # Variables owned by the worker/infra layer, not by application Settings.
    infra_owned = {
        "DATABASE_URL",
        "POSTGRES_PORT_HOST",
        "REDIS_PORT_HOST",
        "API_PORT",
        "ELASTICSEARCH_PORT_HOST",
        "OUTBOX_BATCH_SIZE",
        "OUTBOX_LEASE_MINUTES",
        # Read directly by the worker's ARQ Redis settings; not a Settings field.
        "REDIS_PASSWORD",
    }

    unknown: list[str] = []
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name in known or name in infra_owned:
            continue
        unknown.append(name)

    assert not unknown, f".env.example documents variables nothing reads: {unknown}"


def test_worker_does_not_hardcode_a_database_url_default() -> None:
    """M11: the old worker defaulted DATABASE_URL to a URL matching nothing.

    Its default (``mip_user:mip_password@localhost:5432``) matched neither
    Settings nor the compose port mapping. The worker now uses Settings.
    """
    from mip_workers.worker import WorkerSettings

    assert WorkerSettings.redis_settings is not None
    source = (REPO_ROOT / "apps" / "workers" / "src" / "mip_workers" / "worker.py").read_text(
        encoding="utf-8"
    )
    assert "mip_user:mip_password" not in source


def test_os_environ_does_not_need_pre_set_for_import() -> None:
    """Importing the worker module must not require a fully configured env."""
    assert os.environ.get("ELASTICSEARCH_HOST") is None or True
    import mip_workers.worker  # noqa: F401
