"""Structured logging configuration using structlog.

Produces JSON logs in production (for log aggregators like Datadog/CloudWatch)
and colored, human-readable console logs in development.

Every log entry includes:
- timestamp (ISO 8601)
- log level
- logger name
- request_id (injected by middleware, if present)
- All key-value pairs passed to the logger

NEVER log email content, subject lines, sender addresses, or OAuth tokens.
Logs must contain only operational metadata.
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.common.config import LogFormat


def setup_logging(log_level: str, log_format: LogFormat) -> None:
    """Configure structlog and stdlib logging for the application.

    Args:
        log_level: Python log level string (e.g., "DEBUG", "INFO").
        log_format: Output format — "console" for dev, "json" for production.
    """
    # Shared processors for all environments
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == LogFormat.JSON:
        # Production: JSON output for log aggregators
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Development: colored console output
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Quiet noisy third-party loggers
    for noisy_logger in ("uvicorn.access", "uvicorn.error", "asyncio"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a named structlog logger instance.

    Usage::

        logger = get_logger(__name__)
        logger.info("sync_started", mailbox_id="abc", email_count=42)
    """
    return structlog.get_logger(name)
