"""Application configuration via pydantic-settings.

All configuration is loaded from environment variables (or a .env file in
development). No secrets are ever hardcoded.

Settings are organized into logical groups and composed into a root
Settings class for injection via FastAPI's dependency system.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """Log output format."""

    CONSOLE = "console"  # Human-readable, colored output for development
    JSON = "json"  # Structured JSON for production log aggregators


class Settings(BaseSettings):
    """Root application settings.

    Reads from environment variables with the prefixes defined below.
    Falls back to a .env file in development.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_env: Environment = Environment.DEVELOPMENT
    app_debug: bool = False
    app_log_level: str = "INFO"
    app_log_format: LogFormat = LogFormat.CONSOLE

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "mip"
    postgres_password: str = Field(default="mip_dev_password")
    postgres_db: str = "mail_intelligence"

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # --- Elasticsearch ---
    elasticsearch_host: str = "localhost"
    elasticsearch_port: int = 9200
    elasticsearch_scheme: str = "http"

    # --- Object Storage (MinIO / S3) ---
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_access_key: str = "minioadmin"
    object_storage_secret_key: str = Field(default="minioadmin")
    object_storage_bucket: str = "mail-intelligence"
    object_storage_region: str = "us-east-1"

    # --- CORS ---
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # --- Authentication ---
    jwt_signing_secret: str = Field(
        default="dev-only-change-me-minimum-32-bytes",
        description="Symmetric JWT signing secret for local development.",
    )
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "mail-intelligence-platform"
    jwt_audience: str = "mail-intelligence-api"
    jwt_access_token_expire_minutes: int = 15
    jwt_clock_skew_seconds: int = 60
    jwt_refresh_token_expire_days: int = 30
    session_idle_timeout_hours: int = 8
    session_absolute_timeout_days: int = 30
    session_remember_me_days: int = 90

    @model_validator(mode="after")
    def validate_token_settings(self) -> Settings:
        """Validate token configuration fails closed before runtime use."""
        allowed_algorithms = {"HS256"}
        if self.jwt_algorithm not in allowed_algorithms:
            msg = f"Unsupported JWT algorithm: {self.jwt_algorithm}"
            raise ValueError(msg)

        if len(self.jwt_signing_secret.encode("utf-8")) < 32:
            msg = "JWT signing secret must be at least 32 bytes."
            raise ValueError(msg)

        if self.is_production and self.jwt_signing_secret == "dev-only-change-me-minimum-32-bytes":  # noqa: S105
            msg = "Production JWT signing secret must not use the development default."
            raise ValueError(msg)

        return self

    @property
    def database_url(self) -> str:
        """Async PostgreSQL connection URL for SQLAlchemy."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync PostgreSQL connection URL for Alembic migrations."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis connection URL."""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def elasticsearch_url(self) -> str:
        """Elasticsearch connection URL."""
        return f"{self.elasticsearch_scheme}://{self.elasticsearch_host}:{self.elasticsearch_port}"

    @property
    def is_production(self) -> bool:
        """True if running in production environment."""
        return self.app_env == Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        """True if running in test environment."""
        return self.app_env == Environment.TESTING


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance.

    Using lru_cache ensures settings are read once from the environment
    and reused across the application lifetime.
    """
    return Settings()
