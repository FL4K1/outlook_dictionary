"""MIP Workers — Background task workers (PR-2.4).

Provides OutboxWorker and ElasticsearchMailAdapter for transactional outbox processing.
"""

from mip_workers.es_adapter import (
    ElasticsearchError,
    ElasticsearchMailAdapter,
    IndexResult,
    PermanentElasticsearchError,
    RetryableElasticsearchError,
)
from mip_workers.outbox_worker import OutboxWorker, sanitize_error
from mip_workers.worker import (
    WorkerSettings,
    discover_outbox_events,
    outbox_polling_cron,
    process_outbox_event_job,
)

__all__ = [
    "ElasticsearchError",
    "ElasticsearchMailAdapter",
    "IndexResult",
    "OutboxWorker",
    "PermanentElasticsearchError",
    "RetryableElasticsearchError",
    "WorkerSettings",
    "discover_outbox_events",
    "outbox_polling_cron",
    "process_outbox_event_job",
    "sanitize_error",
]
