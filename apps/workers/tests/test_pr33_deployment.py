"""PR-3.3 deployment contract: compose topology, CI gates, and the worker image.

These tests parse the real deployment artefacts rather than restating them, so a
regression in ``docker-compose.yml``, ``ci.yml``, or the ``Dockerfile`` fails
here instead of only failing in production.

Each test names the defect it prevents. The three headline ones:

  B1 -- the worker was absent from compose and its image installed none of the
        packages it imports, so ``python -m mip_workers`` raised
        ``ModuleNotFoundError`` before ARQ ever started.
  B2 -- the worker silently fell back to ``http://localhost:9200`` when
        Elasticsearch was unconfigured, so it ran healthy while talking to
        nothing.
  M10/H10 -- schema migration never ran, so every database-backed test skipped
        and the API started against an empty schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "infra" / "docker" / "docker-compose.yml"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WORKER_DOCKERFILE = REPO_ROOT / "apps" / "workers" / "Dockerfile"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, Any]:
    return compose["services"]


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ci_test_job(ci: dict[str, Any]) -> dict[str, Any]:
    return ci["jobs"]["test"]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


def _step_names(job: dict[str, Any]) -> list[str]:
    return [s.get("name", s.get("uses", "")) for s in _steps(job)]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in _steps(job):
        if step.get("name") == name:
            return step
    raise AssertionError(f"CI step {name!r} not found; have {_step_names(job)}")


def _run_text(job: dict[str, Any], name: str) -> str:
    return _step(job, name).get("run", "")


# ---------------------------------------------------------------------------
# B1 -- the worker exists and is wired
# ---------------------------------------------------------------------------
def test_worker_service_exists(services: dict[str, Any]) -> None:
    assert "worker" in services, "the ARQ worker is missing from compose"


def test_worker_runs_the_arq_entrypoint(services: dict[str, Any]) -> None:
    command = services["worker"]["command"]
    assert command == ["arq", "mip_workers.worker.WorkerSettings"]


def test_worker_publishes_no_ports(services: dict[str, Any]) -> None:
    """The worker has no HTTP listener; publishing a port would imply one."""
    assert "ports" not in services["worker"]


def test_worker_waits_for_migrations_and_its_dependencies(services: dict[str, Any]) -> None:
    depends = services["worker"]["depends_on"]
    assert depends["migrate"]["condition"] == "service_completed_successfully"
    assert depends["redis"]["condition"] == "service_healthy"
    assert depends["elasticsearch"]["condition"] == "service_healthy"


def test_worker_has_a_restart_policy(services: dict[str, Any]) -> None:
    assert services["worker"].get("restart") == "unless-stopped"


# ---------------------------------------------------------------------------
# M10 / H10 -- migrations run before anything else
# ---------------------------------------------------------------------------
def test_migrate_service_runs_alembic_upgrade(services: dict[str, Any]) -> None:
    migrate = services["migrate"]
    assert migrate["command"] == ["python", "-m", "mip_workers.migrations", "upgrade", "head"]


def test_migrate_service_uses_the_worker_image(services: dict[str, Any]) -> None:
    """Sharing the image keeps the migration environment and the worker identical."""
    build = services["migrate"]["build"]
    assert build["dockerfile"] == "apps/workers/Dockerfile"


def test_migrate_service_does_not_restart(services: dict[str, Any]) -> None:
    assert services["migrate"]["restart"] == "no"


def test_api_waits_for_a_successful_migration(services: dict[str, Any]) -> None:
    depends = services["api"]["depends_on"]
    assert depends["migrate"]["condition"] == "service_completed_successfully"


def test_migrate_waits_for_postgres(services: dict[str, Any]) -> None:
    depends = services["migrate"]["depends_on"]
    assert depends["postgres"]["condition"] == "service_healthy"


def test_every_long_running_service_restarts(services: dict[str, Any]) -> None:
    for name in ("postgres", "redis", "elasticsearch", "api", "worker"):
        assert services[name].get("restart") == "unless-stopped", name


def test_every_depends_on_uses_an_explicit_condition(services: dict[str, Any]) -> None:
    """`depends_on` without a condition is start-order only, not readiness."""
    for name, spec in services.items():
        for dep, cfg in (spec.get("depends_on") or {}).items():
            assert "condition" in cfg, f"{name} -> {dep} has no readiness condition"


def test_every_stateful_service_has_a_healthcheck(services: dict[str, Any]) -> None:
    for name in ("postgres", "redis", "elasticsearch", "api"):
        assert "healthcheck" in services[name], name


def test_elasticsearch_healthcheck_accepts_yellow(services: dict[str, Any]) -> None:
    """A single-node cluster is legitimately yellow; requiring green never passes."""
    test = str(services["elasticsearch"]["healthcheck"]["test"])
    assert "yellow" in test and "green" in test


# ---------------------------------------------------------------------------
# B2 -- Elasticsearch host is explicit, never a localhost fallback
# ---------------------------------------------------------------------------
def test_elasticsearch_host_is_set_explicitly_for_every_app_service(
    services: dict[str, Any],
) -> None:
    for name in ("api", "worker", "migrate"):
        env = services[name]["environment"]
        assert env.get("ELASTICSEARCH_HOST") == "elasticsearch", name


def test_no_service_points_at_localhost_elasticsearch(services: dict[str, Any]) -> None:
    for name, spec in services.items():
        env = spec.get("environment") or {}
        if isinstance(env, list):
            joined = " ".join(env)
        else:
            joined = " ".join(f"{k}={v}" for k, v in env.items())
        assert "localhost:9200" not in joined, f"{name} hardcodes localhost:9200"


def _config_lines() -> str:
    """The compose file with comments stripped.

    The comments legitimately *name* the removed ``localhost:9200`` fallback in
    order to explain why it is gone, so only real config may be checked.
    """
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    return "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))


def test_localhost_elasticsearch_appears_only_in_a_container_self_healthcheck() -> None:
    """``localhost:9200`` is correct inside the ES container's own healthcheck.

    It is wrong anywhere else, because from another container ``localhost`` is
    that container. Only healthchecks are exempt; every cross-service address
    must use the compose service name.
    """
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    offenders: list[str] = []
    for name, spec in compose["services"].items():
        env = spec.get("environment") or {}
        pairs = env.items() if isinstance(env, dict) else [(e, "") for e in env]
        for key, value in pairs:
            if "localhost:9200" in str(value):
                offenders.append(f"{name}.{key}")
        for section in ("command", "entrypoint"):
            if "localhost:9200" in str(spec.get(section)):
                offenders.append(f"{name}.{section}")
    assert offenders == [], offenders

    # The one legitimate occurrence: Elasticsearch checking itself.
    assert "localhost:9200" in str(compose["services"]["elasticsearch"]["healthcheck"]["test"])


def test_elasticsearch_port_is_not_published_to_the_host_by_default(
    services: dict[str, Any],
) -> None:
    """The ES port is exposed for local debugging only."""
    ports = services["elasticsearch"]["ports"]
    assert ports == ["${ELASTICSEARCH_PORT:-9200}:9200"]


# ---------------------------------------------------------------------------
# Shared environment contract
# ---------------------------------------------------------------------------
def test_shared_env_declares_the_worker_knobs(
    compose: dict[str, Any], services: dict[str, Any]
) -> None:
    env = services["worker"]["environment"]
    for key in (
        "ELASTICSEARCH_HOST",
        "ELASTICSEARCH_PORT",
        "ELASTICSEARCH_SCHEME",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_DIMENSION",
        "EMBEDDING_MODEL",
        "REDIS_HOST",
        "REDIS_PORT",
        "DATABASE_URL",
    ):
        assert key in env, f"worker is missing {key}"


def test_embedding_dimension_has_one_shared_value(compose: dict[str, Any]) -> None:
    """M7: producer, provisioning, and retrieval must agree on the dimension."""
    env = compose["services"]["worker"]["environment"]
    assert env["EMBEDDING_DIMENSION"] == "${EMBEDDING_DIMENSION:-1536}"


def test_secrets_are_required_not_defaulted(services: dict[str, Any]) -> None:
    """A defaulted signing key or DEK is a silent production security hole.

    Compose's ``${VAR:?message}`` form makes the stack refuse to start rather
    than booting with a well-known dev credential.
    """
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "${JWT_SIGNING_SECRET:?" in raw
    assert "${ENCRYPTION_DEK:?" in raw

    api_env = services["api"]["environment"]
    worker_env = services["worker"]["environment"]

    # The DEK is required by both: the API encrypts credentials and the worker
    # must decrypt them to refresh mail tokens.
    assert api_env["ENCRYPTION_DEK"].startswith("${ENCRYPTION_DEK:?")
    assert worker_env["ENCRYPTION_DEK"].startswith("${ENCRYPTION_DEK:?")

    # Only the API verifies JWTs; the worker has no reason to hold a signing key.
    assert api_env["JWT_SIGNING_SECRET"].startswith("${JWT_SIGNING_SECRET:?")
    assert "JWT_SIGNING_SECRET" not in worker_env


def test_declared_volumes_cover_all_stateful_services(compose: dict[str, Any]) -> None:
    assert set(compose["volumes"]) == {
        "postgres_data",
        "redis_data",
        "elasticsearch_data",
    }
    for name, path in (
        ("postgres", "/var/lib/postgresql/data"),
        ("redis", "/data"),
        ("elasticsearch", "/usr/share/elasticsearch/data"),
    ):
        assert any(path in v for v in compose["services"][name]["volumes"]), name


# ---------------------------------------------------------------------------
# Worker image
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def dockerfile() -> str:
    return WORKER_DOCKERFILE.read_text(encoding="utf-8")


def test_image_installs_every_package_the_worker_imports(dockerfile: str) -> None:
    for package in (
        "/opt/packages/models",
        "/opt/packages/providers",
        "/opt/packages/ai",
        "/opt/packages/email-parser",
    ):
        assert package in dockerfile, package
    # B1: the worker imports app.* (repositories, sync_orchestrator, common).
    assert "pip install --no-cache-dir /opt/api" in dockerfile


def test_image_installs_the_worker_package_with_its_source(dockerfile: str) -> None:
    """hatchling needs src/ present at build time, not only pyproject.toml."""
    assert "COPY apps/workers/src/ /opt/app/src/" in dockerfile
    assert "COPY apps/workers/pyproject.toml /opt/app/" in dockerfile


def test_image_ships_the_alembic_environment(dockerfile: str) -> None:
    assert "COPY apps/api/alembic.ini" in dockerfile
    assert "COPY apps/api/alembic/ /opt/migrate/alembic/" in dockerfile
    assert "MIP_ALEMBIC_DIR=/opt/migrate/alembic" in dockerfile
    assert "alembic>=1.13.0" in dockerfile


def test_image_runs_as_non_root(dockerfile: str) -> None:
    assert "useradd" in dockerfile
    user_lines = [ln for ln in dockerfile.splitlines() if ln.strip().startswith("USER ")]
    assert user_lines, "the image never drops root"
    assert user_lines[-1].split()[1] != "root"


def test_image_has_a_redis_backed_healthcheck(dockerfile: str) -> None:
    assert "HEALTHCHECK" in dockerfile
    assert "check_health" in dockerfile


def test_worker_package_declares_its_imports(dockerfile: str) -> None:
    import tomllib

    pyproject = tomllib.loads(
        (REPO_ROOT / "apps" / "workers" / "pyproject.toml").read_text(encoding="utf-8")
    )
    deps = pyproject["project"]["dependencies"]
    for required in ("mip-ai", "mip-api", "arq>=0.28.0", "httpx>=0.27.0"):
        assert any(required in d for d in deps), required


# ---------------------------------------------------------------------------
# CI gates
# ---------------------------------------------------------------------------
def test_ci_builds_and_smoke_tests_the_worker_image(ci: dict[str, Any]) -> None:
    docker_job = ci["jobs"]["docker"]
    raw = yaml.safe_dump(docker_job)
    assert "apps/workers/Dockerfile" in raw
    assert "mip-workers:ci" in raw
    assert "import mip_workers.worker" in raw


def test_ci_installs_the_worker_package(ci_test_job: dict[str, Any]) -> None:
    install = _run_text(ci_test_job, "Install dependencies")
    assert 'pip install -e "apps/workers"' in install
    assert 'pip install -e "apps/api[dev]"' in install


def test_ci_provisions_postgres_redis_and_elasticsearch(ci_test_job: dict[str, Any]) -> None:
    names = set(ci_test_job["services"])
    assert {"postgres", "redis", "elasticsearch"} <= names


def test_ci_uses_the_vendor_elasticsearch_image(ci_test_job: dict[str, Any]) -> None:
    """The Docker Hub `elasticsearch` image is a community repack, not the vendor build."""
    image = ci_test_job["services"]["elasticsearch"]["image"]
    assert image.startswith("docker.elastic.co/elasticsearch/elasticsearch:")


def test_ci_matches_the_compose_elasticsearch_version(ci_test_job: dict[str, Any]) -> None:
    compose_es = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    compose_image = compose_es["services"]["elasticsearch"]["image"]
    assert ci_test_job["services"]["elasticsearch"]["image"] == compose_image


def test_ci_applies_migrations_before_running_tests(ci_test_job: dict[str, Any]) -> None:
    names = _step_names(ci_test_job)
    migrate_at = names.index("Apply database migrations")
    test_at = names.index("Run full test suite")
    assert migrate_at < test_at


def test_ci_verifies_the_migration_is_at_head(ci_test_job: dict[str, Any]) -> None:
    run = _run_text(ci_test_job, "Verify migration is at head")
    assert "migrations current" in run
    assert "migrations head" in run


def test_ci_smoke_tests_the_worker_import(ci_test_job: dict[str, Any]) -> None:
    run = _run_text(ci_test_job, "Worker import smoke test")
    assert "import mip_workers.worker" in run
    assert "WorkerSettings" in run


def test_ci_asserts_the_worker_refuses_an_unconfigured_es_host(ci_test_job: dict[str, Any]) -> None:
    """B2 regression gate for the removed localhost:9200 fallback."""
    run = _run_text(ci_test_job, "Worker fails fast without ELASTICSEARCH_HOST")
    assert "env -u ELASTICSEARCH_HOST" in run
    assert "ElasticsearchConfigurationError" in run
    assert "SystemExit" in run


def test_ci_sets_an_explicit_elasticsearch_host_for_tests(ci_test_job: dict[str, Any]) -> None:
    env = ci_test_job["env"]
    assert env["ELASTICSEARCH_HOST"] == "localhost"
    assert env["ELASTICSEARCH_PORT"] == "9200"


def test_ci_enforces_zero_skips_on_release_critical_suites(ci_test_job: dict[str, Any]) -> None:
    run = _run_text(ci_test_job, "Enforce zero skips in release-critical suites")
    assert "if skipped:" in run
    assert "sys.exit(1)" in run
    assert "if total == 0:" in run
    assert (
        _step(ci_test_job, "Enforce zero skips in release-critical suites").get("if") == "always()"
    )


def test_release_critical_run_includes_the_worker_suite(ci_test_job: dict[str, Any]) -> None:
    run = _run_text(ci_test_job, "Release-critical suites (zero skips enforced)")
    for path in (
        "apps/workers/tests",
        "apps/api/tests/integration/test_e2e_mail_pipeline.py",
        "apps/api/tests/integration/test_hybrid_search.py",
    ):
        assert path in run, path


def test_ci_runs_on_pull_requests(ci: dict[str, Any]) -> None:
    # YAML 1.1 parses the bare key `on:` as the boolean True.
    triggers = ci.get("on", ci.get(True))
    assert "pull_request" in triggers
    assert "push" in triggers


def test_ci_lints_and_typechecks_the_worker(ci: dict[str, Any]) -> None:
    typecheck_run = _run_text(ci["jobs"]["typecheck"], "MyPy")
    assert "-p mip_workers" in typecheck_run
    lint_run = _run_text(ci["jobs"]["lint"], "Ruff check")
    assert lint_run.strip() == "ruff check ."


def test_ci_pins_ruff_exactly(ci: dict[str, Any]) -> None:
    """An unpinned linter makes CI results non-reproducible."""
    install = _run_text(ci["jobs"]["lint"], "Install ruff")
    assert "ruff==" in install


# ---------------------------------------------------------------------------
# .env.example
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def env_example() -> dict[str, str]:
    """Parse the documented variables, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_env_example_does_not_declare_a_duplicate_es_url(env_example: dict[str, str]) -> None:
    """``Settings.elasticsearch_url`` is canonical and is derived from the host/port."""
    assert "ELASTICSEARCH_URL" not in env_example


def test_env_example_documents_the_worker_knobs(env_example: dict[str, str]) -> None:
    for key in (
        "ELASTICSEARCH_HOST",
        "ELASTICSEARCH_PORT",
        "ELASTICSEARCH_SCHEME",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_DIMENSION",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "OUTBOX_BATCH_SIZE",
        "OUTBOX_LEASE_MINUTES",
    ):
        assert key in env_example, key


def test_env_example_variables_are_actually_consumed(env_example: dict[str, str]) -> None:
    """Every documented variable must be read by something, or it is a silent no-op.

    A variable is legitimately consumed by exactly one of three readers:
    the canonical API ``Settings``, the worker's own environment lookups, or
    docker-compose interpolation. A name that matches none of them is drift --
    operators set it, believe it took effect, and it never does.
    """
    from app.common.config import Settings

    # Settings is case-insensitive with no prefix, so an environment variable
    # name matches a field when they are equal ignoring case.
    consumed_by_settings = {name.lower() for name in Settings.model_fields}

    # Read directly from os.environ by the worker and its migration entrypoint,
    # rather than through Settings.
    consumed_by_worker = {
        "ELASTICSEARCH_HOST",
        "ELASTICSEARCH_PORT",
        "ELASTICSEARCH_SCHEME",
        "OUTBOX_BATCH_SIZE",
        "OUTBOX_LEASE_MINUTES",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "REDIS_DB",
        "DATABASE_URL",
    }

    # Interpolated by compose as ${NAME:-default} to set a published host port.
    consumed_by_compose = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", _config_lines()))

    unknown = sorted(
        key
        for key in env_example
        if key.isupper()
        and key.lower() not in consumed_by_settings
        and key not in consumed_by_worker
        and key not in consumed_by_compose
    )
    assert unknown == [], f"documented but consumed by nothing: {unknown}"


def test_env_example_does_not_embed_real_secrets(env_example: dict[str, str]) -> None:
    """Placeholders must be obviously fake, so nobody ships them to production."""
    markers = (
        "change",
        "example",
        "dev-only",
        "dev_",
        "your-",
        "xxx",
        "<",
        "generate",
        "replace",
        "placeholder",
    )
    for key in ("JWT_SIGNING_SECRET", "ENCRYPTION_DEK", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        assert key in env_example, f"{key} is undocumented"
        value = env_example[key]
        # An empty optional secret is a correct, honest default.
        if not value:
            continue
        assert len(value) >= 8, f"{key} is too short to be a usable placeholder"
        lowered = value.lower()
        obvious = any(m in lowered for m in markers)
        # A single repeated character (an all-zero DEK) is the conventional
        # "obviously not a real key" dev placeholder.
        uniform = len(set(value)) == 1
        assert obvious or uniform, f"{key} looks like a real credential: {value!r}"


def test_env_example_is_valid_utf8_json_free_of_control_characters() -> None:
    """Guards against a corrupted example file that would mislead operators."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert not any(ord(ch) < 32 and ch != "\n" for ch in text)
    json.dumps(text)  # must be serialisable without surrogates
