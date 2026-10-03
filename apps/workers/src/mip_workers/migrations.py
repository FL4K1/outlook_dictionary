"""Alembic migration runner for the worker image (PR-3.3 / M10, H10).

Why this exists
---------------
Before PR-3.3 there was no migration execution path anywhere in the
repository:

* ``apps/api/Dockerfile`` started uvicorn directly.
* ``infra/docker/docker-compose.yml`` had no migrate step or service.
* ``apps/api/alembic.ini`` hardcodes a localhost development URL.
* ``apps/api/alembic/env.py`` resolves the URL *only* from the Alembic config
  and never reads application ``Settings``, so a production ``alembic upgrade
  head`` would have targeted the wrong database.

``apps/api/alembic/env.py`` is out of this slice's ownership, so rather than
modify it we drive Alembic through its Python API and explicitly inject
``sqlalchemy.url`` derived from ``DATABASE_URL``. ``env.py`` then reads exactly
what we supplied.

Run with::

    python -m mip_workers.migrations
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Async SQLAlchemy driver required by ``alembic/env.py``, which builds its
#: engine with ``async_engine_from_config``.
_ASYNC_DRIVER = "asyncpg"


class MigrationConfigurationError(RuntimeError):
    """Raised when the migration environment is not correctly configured."""


def resolve_alembic_dir(environ: dict[str, str] | None = None) -> Path:
    """Locate the Alembic script directory.

    Resolution order:
        1. ``MIP_ALEMBIC_DIR`` (set by the worker Dockerfile).
        2. ``apps/api/alembic`` relative to the repository root, found by
           walking up from this file looking for the root ``pyproject.toml``.
        3. ``apps/api/alembic`` relative to the current working directory.

    Raises:
        MigrationConfigurationError: If no script directory can be found.
    """
    env = os.environ if environ is None else environ

    explicit = env.get("MIP_ALEMBIC_DIR")
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_dir():
            msg = f"MIP_ALEMBIC_DIR={explicit!r} does not point at a directory."
            raise MigrationConfigurationError(msg)
        return candidate

    here = Path(__file__).resolve()
    for parent in here.parents:
        root_marker = parent / "pyproject.toml"
        repo_alembic = parent / "apps" / "api" / "alembic"
        if root_marker.is_file() and repo_alembic.is_dir():
            return repo_alembic

    cwd_candidate = Path.cwd() / "apps" / "api" / "alembic"
    if cwd_candidate.is_dir():
        return cwd_candidate

    msg = (
        "Could not locate the Alembic script directory. "
        "Set MIP_ALEMBIC_DIR to the directory containing env.py."
    )
    raise MigrationConfigurationError(msg)


def resolve_database_url(environ: dict[str, str] | None = None) -> str:
    """Return the async database URL for migrations.

    Resolution order:
        1. ``DATABASE_URL``, when explicitly set.
        2. ``Settings.database_url``, which the API and the worker also use.

    Falling back to ``Settings`` matters: ``Settings`` has no ``database_url``
    *field* (it is a property composed from ``POSTGRES_*``), so a deployment that
    configured only the canonical ``POSTGRES_*`` variables would otherwise have
    no migration URL at all -- and could have had a *different* migration URL
    from its application URL, which is the failure mode M10 exists to prevent.

    The result is normalised onto the ``asyncpg`` driver because
    ``alembic/env.py`` requires an async engine.

    Raises:
        MigrationConfigurationError: If no URL can be determined, or if the URL
            does not use the postgresql driver.
    """
    env = os.environ if environ is None else environ

    url = (env.get("DATABASE_URL") or "").strip()
    if not url:
        try:
            from app.common.config import get_settings

            url = (get_settings().database_url or "").strip()
        except Exception as exc:
            msg = (
                "DATABASE_URL is not set and the application Settings could not be "
                f"loaded to derive it: {exc}"
            )
            raise MigrationConfigurationError(msg) from exc

    if not url:
        msg = "DATABASE_URL is required to run migrations."
        raise MigrationConfigurationError(msg)

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", f"postgresql+{_ASYNC_DRIVER}://", 1)
    elif url.startswith(f"postgresql+{_ASYNC_DRIVER}://"):
        pass
    else:
        msg = (
            "DATABASE_URL must use the postgresql driver with the asyncpg dialect "
            f"for migrations, got {url.split(':', 1)[0]!r}."
        )
        raise MigrationConfigurationError(msg)

    return url


def build_alembic_config(
    database_url: str | None = None,
    script_location: Path | None = None,
    environ: dict[str, str] | None = None,
) -> object:
    """Build an Alembic ``Config`` wired to the resolved URL and script dir.

    A programmatic ``Config`` leaves ``config_file_name`` as ``None``, which makes
    ``alembic/env.py`` skip its ``fileConfig()`` call. That is intentional: we do
    not want migrations to reconfigure the application's logging.

    The underlying ``ConfigParser`` is created with ``interpolation=None``.
    Alembic's default parser applies ``%``-interpolation, which corrupts any
    database password containing a percent sign -- and raises
    ``ValueError: invalid interpolation syntax`` outright. Injecting the URL as a
    literal is the only correct way to pass an operator-supplied credential.
    """
    import configparser

    from alembic.config import Config

    env = os.environ if environ is None else environ
    url = database_url if database_url is not None else resolve_database_url(env)
    script_dir = script_location if script_location is not None else resolve_alembic_dir(env)

    config = Config()
    config.file_config = configparser.ConfigParser(interpolation=None)
    config.set_main_option("script_location", str(script_dir))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade_to_head(
    database_url: str | None = None,
    script_location: Path | None = None,
    revision: str = "head",
) -> None:
    """Run migrations up to ``revision`` (default ``head``)."""
    from alembic import command

    command.upgrade(build_alembic_config(database_url, script_location), revision)


def current_revision(
    database_url: str | None = None,
    script_location: Path | None = None,
) -> str | None:
    """Return the currently applied revision, or ``None`` on a fresh database."""
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    config = build_alembic_config(database_url, script_location)
    sync_url = config.get_main_option("sqlalchemy.url")
    if sync_url and f"+{_ASYNC_DRIVER}://" in sync_url:
        sync_url = sync_url.replace(f"+{_ASYNC_DRIVER}://", "://", 1)

    engine = create_engine(sync_url or "")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            return context.get_current_revision()
    finally:
        engine.dispose()


def _available_revisions(script_location: Path | None = None) -> list[str]:
    """Return the revision identifiers known to the script directory."""
    from alembic.script import ScriptDirectory

    directory = script_location if script_location is not None else resolve_alembic_dir()
    return [
        revision.revision
        for revision in ScriptDirectory.from_config(
            build_alembic_config(script_location=directory)
        ).walk_revisions()
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m mip_workers.migrations``.

    This is a command-line tool, so writing to stdout/stderr is the intended
    behaviour rather than stray debug output (hence the T201 exemptions).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    command_name = args[0] if args else "upgrade"

    try:
        if command_name == "upgrade":
            revision = args[1] if len(args) > 1 else "head"
            upgrade_to_head(revision=revision)
        elif command_name == "current":
            print(current_revision() or "<none>")  # noqa: T201
        elif command_name == "head":
            for revision in _available_revisions():
                print(revision)  # noqa: T201
        else:
            msg = f"Unknown migrations command {command_name!r}. Use upgrade|current|head."
            print(msg, file=sys.stderr)  # noqa: T201
            return 2
    except MigrationConfigurationError as exc:
        print(f"migrations: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
