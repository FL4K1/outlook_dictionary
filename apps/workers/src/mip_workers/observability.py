"""Structured logging and metrics for the background worker (PR-3.3 / H8).

Two responsibilities:

1. **Structured logging.** Before PR-3.3 the worker process never called
   :func:`app.common.logging.setup_logging`, so every line the worker emitted
   was an unstructured stdlib string that no aggregator could parse. The worker
   now uses the *same* canonical logging configuration as the API so that
   ``request_id`` binding, JSON rendering, and field naming are identical
   across both processes.

2. **The six release-critical counters.** The PR-3.3 audit found
   ``prometheus-client`` declared as an API dependency and then entirely unused:
   there was no metric of any kind in the repository. These six counters map
   one-to-one onto the failure branches that an operator needs to alert on,
   and they are the minimum set that makes a sync or outbox stall visible.

Privacy boundary
----------------
Counters carry only **bounded, non-identifying labels**: a small closed set of
category strings. There are deliberately:

* no ``tenant_id``, user id, mailbox address, or folder id labels -- these are
  unbounded cardinality (they would break the metrics backend) and are
  identifiers rather than operational signals;
* no email body, subject, body preview, LLM prompt, LLM completion, API key,
  OAuth token, or sync token in any metric or log field.

Per-request identifiers (``request_id``, ``tenant_id``) belong in *logs*, where
they are already structured and already bound, not in metric labels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from prometheus_client import REGISTRY, Counter, Gauge, generate_latest

if TYPE_CHECKING:
    from collections.abc import Mapping

# Metric name constants. Exported so tests and the PR-3.3 verification script
# can assert on the exact registered names rather than hard-coding strings.
SYNC_SUCCESS = "sync_success"
SYNC_FAILURE = "sync_failure"
SYNC_AUTH_REQUIRED = "sync_auth_required"
OUTBOX_SUCCESS = "outbox_success"
OUTBOX_FAILURE = "outbox_failure"
OUTBOX_DEAD_LETTER = "outbox_dead_letter"

#: Extras of ``{"outbox": ..., "sync": ...}`` -- values are low-cardinality
#: outcome categories, never identifiers or content.
_METRIC_LABELS = ("outbox", "sync")


def _counter(name: str, documentation: str) -> Counter:
    """Create a Counter, reusing the existing collector on re-import.

    ``prometheus_client`` raises ``ValueError`` on duplicate registration, which
    happens under test re-import or when both the API and worker import this
    module in one interpreter. Reuse is the correct behaviour: the counter
    object is the same, so no series are lost.
    """
    try:
        return Counter(name, documentation, _METRIC_LABELS)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is None:  # pragma: no cover - defensive
            raise
        return existing  # type: ignore[no-any-return]


SYNC_SUCCESS_COUNTER = _counter(
    SYNC_SUCCESS,
    "Mail folder sync runs that completed without error.",
)
SYNC_FAILURE_COUNTER = _counter(
    SYNC_FAILURE,
    "Mail folder sync runs that terminated in an error state.",
)
SYNC_AUTH_REQUIRED_COUNTER = _counter(
    SYNC_AUTH_REQUIRED,
    "Mail folder sync runs that require re-consent or a credential refresh.",
)
OUTBOX_SUCCESS_COUNTER = _counter(
    OUTBOX_SUCCESS,
    "Outbox events successfully projected into Elasticsearch.",
)
OUTBOX_FAILURE_COUNTER = _counter(
    OUTBOX_FAILURE,
    "Outbox events that failed and remain recoverable.",
)
OUTBOX_DEAD_LETTER_COUNTER = _counter(
    OUTBOX_DEAD_LETTER,
    "Outbox events abandoned to DEAD_LETTER after exhausting retries.",
)

OUTBOX_PENDING_GAUGE = Gauge(
    "outbox_pending_events",
    "Outbox events currently eligible or in flight, refreshed by the poller.",
)

#: Frozen set of ``outbox`` label values. Guards against accidentally
#: introducing an unbounded label at a call site.
OUTBOX_LABEL_VALUES = frozenset(
    {"done", "dead_letter", "retryable", "unknown", "enqueue_failed", "skipped"}
)
#: Frozen set of ``sync`` label values.
SYNC_LABEL_VALUES = frozenset({"ok", "error", "auth_required", "lease_lost", "rate_limited"})


def _validated(counter: Counter, outbox: str, sync: str) -> None:
    """Reject out-of-vocabulary label values before they reach the registry.

    An unbounded label is a metrics outage. Failing loudly in development is
    strictly better than silently shipping one.
    """
    if outbox not in OUTBOX_LABEL_VALUES:
        msg = f"outbox label must be one of {sorted(OUTBOX_LABEL_VALUES)}, got {outbox!r}."
        raise ValueError(msg)
    if sync not in SYNC_LABEL_VALUES:
        msg = f"sync label must be one of {sorted(SYNC_LABEL_VALUES)}, got {sync!r}."
        raise ValueError(msg)


def record_sync_success() -> None:
    """Record a completed mail folder sync."""
    _validated(SYNC_SUCCESS_COUNTER, outbox="done", sync="ok")
    SYNC_SUCCESS_COUNTER.labels(outbox="done", sync="ok").inc()


def record_sync_failure(reason: str = "error") -> None:
    """Record a sync that ended in an error state.

    Args:
        reason: One of :data:`SYNC_LABEL_VALUES`. Callers pass a fixed category,
            never a provider error string.
    """
    if reason not in SYNC_LABEL_VALUES:
        reason = "error"
    _validated(SYNC_FAILURE_COUNTER, outbox="done", sync=reason)
    SYNC_FAILURE_COUNTER.labels(outbox="done", sync=reason).inc()


def record_sync_auth_required() -> None:
    """Record a sync that requires re-consent or credential refresh."""
    _validated(SYNC_AUTH_REQUIRED_COUNTER, outbox="done", sync="auth_required")
    SYNC_AUTH_REQUIRED_COUNTER.labels(outbox="done", sync="auth_required").inc()


def record_outbox_success() -> None:
    """Record an outbox event successfully projected into Elasticsearch."""
    _validated(OUTBOX_SUCCESS_COUNTER, outbox="done", sync="ok")
    OUTBOX_SUCCESS_COUNTER.labels(outbox="done", sync="ok").inc()


def record_outbox_failure(reason: str = "retryable") -> None:
    """Record an outbox event that failed but remains recoverable.

    Args:
        reason: One of :data:`OUTBOX_LABEL_VALUES`.
    """
    if reason not in OUTBOX_LABEL_VALUES:
        reason = "unknown"
    _validated(OUTBOX_FAILURE_COUNTER, outbox=reason, sync="ok")
    OUTBOX_FAILURE_COUNTER.labels(outbox=reason, sync="ok").inc()


def record_outbox_dead_letter() -> None:
    """Record an outbox event abandoned to DEAD_LETTER."""
    _validated(OUTBOX_DEAD_LETTER_COUNTER, outbox="dead_letter", sync="ok")
    OUTBOX_DEAD_LETTER_COUNTER.labels(outbox="dead_letter", sync="ok").inc()


def set_outbox_pending(count: int) -> None:
    """Publish the number of currently eligible / in-flight outbox events."""
    OUTBOX_PENDING_GAUGE.set(max(0, int(count)))


def render_metrics() -> bytes:
    """Return the Prometheus exposition payload for the worker process."""
    return generate_latest(REGISTRY)


def configure_worker_logging(log_level: str, log_format: str) -> None:
    """Apply the canonical application logging configuration to the worker.

    Delegates to the same :func:`app.common.logging.setup_logging` the API uses so
    that both processes emit the same JSON envelope with the same field names.

    Args:
        log_level: Python log level name, e.g. ``"INFO"``.
        log_format: ``"json"`` or ``"console"``.
    """
    from app.common.config import LogFormat
    from app.common.logging import setup_logging

    try:
        resolved = LogFormat(log_format)
    except ValueError:
        resolved = LogFormat.CONSOLE
    setup_logging(log_level, resolved)


def get_worker_logger(name: str) -> Any:
    """Return a structlog logger bound to the worker's component name."""
    from app.common.logging import get_logger

    return get_logger(name)


def safe_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the safe, non-identifying subset of a field mapping.

    Used at log call sites that build dynamic field dictionaries, so an
    accidental ``**exc.__dict__`` cannot smuggle a token into the log stream.
    """
    allowlist = {
        "request_id",
        "correlation_id",
        "event_id",
        "event_type",
        "tenant_id",
        "mail_account_id",
        "mail_folder_id",
        "message_id",
        "version",
        "status",
        "state",
        "attempt",
        "attempts",
        "next_attempt_in_seconds",
        "status_code",
        "error_class",
        "outcome",
        "reason",
        "duration_ms",
        "count",
        "page",
        "has_more",
        "resync_generation",
        "dimensions",
        "index",
        "index_provisioned",
    }
    return {key: value for key, value in fields.items() if key in allowlist}


#: Names that must never appear in worker log output. Asserted by test.
FORBIDDEN_LOG_SUBSTRINGS: tuple[str, ...] = (
    "access_token",
    "refresh_token",
    "api_key",
    "Authorization",
    "Bearer ",
    "sync_token",
    "client_secret",
    "encryption_dek",
)


def assert_no_secrets_in_payload(payload: str) -> None:
    """Raise if a serialized log payload contains a secret-shaped substring.

    Defensive last line of defence for the privacy boundary. Intended for use
    in tests and in the worker's debug logging path.
    """
    lowered = payload.lower()
    for needle in FORBIDDEN_LOG_SUBSTRINGS:
        if needle.lower() in lowered:
            msg = f"Refusing to emit log payload containing {needle!r}."
            raise ValueError(msg)


logging.getLogger(__name__).addHandler(logging.NullHandler())
