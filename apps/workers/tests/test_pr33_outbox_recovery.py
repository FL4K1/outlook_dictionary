"""PR-3.3 outbox recovery, scheduling identity, and error classification.

Covers:
  M1 -- deterministic job identity replaces unbounded duplicate scheduling
  M2 -- the enqueue/durable-transition ordering invariant and its crash window
  M3 -- explicit error classification instead of a blanket dead-letter

These tests run without external services by modelling the two collaborators the
ordering guarantee depends on: a session whose transaction state is observable,
and a queue that implements the *actual* ARQ 0.28.0 ``enqueue_job`` contract.

That contract was read from the installed package rather than assumed
(``arq/connections.py::ArqRedis.enqueue_job``)::

    job_id = _job_id or uuid4().hex
    job_key = job_key_prefix + job_id
    async with self.pipeline(transaction=True) as pipe:
        await pipe.watch(job_key)
        if await pipe.exists(job_key, result_key_prefix + job_id):
            await pipe.reset()
            return None

and ``arq/worker.py::Worker.finish_job`` deletes ``job_key_prefix + job_id`` while
writing ``result_key_prefix + job_id`` with a ``keep_result`` TTL. Deduplication
is therefore bounded by that TTL, not permanent -- which is exactly why the job
id is scoped to a lease version rather than to the event alone.

A live-Redis ARQ test is included and runs in CI; it skips locally when Redis is
unavailable, and FAILS (never skips) under ``CI=true``.
"""

from __future__ import annotations

import inspect
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from mip_workers.es_adapter import (
    IndexResult,
    PermanentElasticsearchError,
    RetryableElasticsearchError,
)
from mip_workers.outbox_worker import OutboxWorker, embed_job_id, outbox_job_id

from mip_models.mail import OutboxEventStatus

REDIS_TEST_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0")
CI = os.getenv("CI") == "true"


# ---------------------------------------------------------------------------
# Collaborator doubles
# ---------------------------------------------------------------------------
class RecordingSession:
    """A session whose transaction state is observable and controllable.

    ``commit_hook`` models durability: a staged write only survives once the
    commit returns, which is what makes the crash-window test meaningful.
    """

    def __init__(self, *, fail_commit_on: int | None = None) -> None:
        self.log: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.commit_hook: Any = None
        self._in_txn = False
        self._fail_commit_on = fail_commit_on

    def in_transaction(self) -> bool:
        return self._in_txn

    def begin_write(self) -> None:
        self._in_txn = True

    async def commit(self) -> None:
        self.commits += 1
        if self._fail_commit_on is not None and self.commits == self._fail_commit_on:
            # Simulate a crash at the durability boundary: the write is lost.
            self._in_txn = False
            self.log.append("commit_failed")
            raise RuntimeError("simulated crash during commit")
        self._in_txn = False
        self.log.append("commit")
        if self.commit_hook is not None:
            await self.commit_hook()

    async def rollback(self) -> None:
        self.rollbacks += 1
        self._in_txn = False
        self.log.append("rollback")


class RecordingOutboxRepo:
    """Outbox repository separating *staged* from *durably persisted* transitions."""

    def __init__(self, session: RecordingSession, event: Any) -> None:
        self.session = session
        self.event = event
        self.persisted: dict[str, Any] = {}
        self._staged: dict[str, Any] | None = None

    async def acquire_outbox_lease(
        self, event_id: uuid.UUID, worker_id: uuid.UUID, lease_duration: Any
    ) -> int | None:
        self.event.locked_by = worker_id
        self.event.lease_version += 1
        return self.event.lease_version

    async def get(self, event_id: uuid.UUID) -> Any:
        return self.event

    async def update_status_cas(
        self,
        event_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
        new_status: Any = None,
        last_error: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> bool:
        self.session.begin_write()
        self._staged = {
            "status": str(new_status),
            "last_error": last_error,
            "next_attempt_at": next_attempt_at,
        }
        self.session.log.append(f"cas:{new_status}")
        return True

    async def flush(self) -> None:
        """Model durability: a transition only survives once committed."""
        if self._staged is not None:
            self.persisted.update(self._staged)
            self._staged = None


class ArqLikeQueue:
    """Implements the real ARQ 0.28.0 ``enqueue_job`` deduplication contract."""

    def __init__(self, log: list[str] | None = None) -> None:
        self.live_jobs: set[str] = set()
        self.results: set[str] = set()
        self.enqueued: list[tuple[str, tuple[Any, ...], str]] = []
        self.fail_times = 0
        self.tx_open_during_enqueue: list[bool] = []
        self.log = log

    async def enqueue_job(
        self, function: str, *args: Any, _job_id: str | None = None, **kwargs: Any
    ) -> Any:
        if _job_id is not None and (_job_id in self.live_jobs or _job_id in self.results):
            return None
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("simulated Redis outage during enqueue")
        self.enqueued.append((function, args, _job_id or ""))
        if _job_id:
            self.live_jobs.add(_job_id)
        if self.log is not None:
            self.log.append("enqueue")
        return object()

    def complete(self, job_id: str) -> None:
        self.live_jobs.discard(job_id)
        self.results.add(job_id)


class ScriptedEsAdapter:
    """Elasticsearch adapter that raises a scripted error or reports a result."""

    def __init__(self, error: Exception | None = None, *, success: bool = True) -> None:
        self.error = error
        self.success = success
        self.calls: list[dict[str, Any]] = []
        self.base_url = "http://es.invalid:9200"

    async def index_message(
        self, index_name: str, document: dict[str, Any], version: int
    ) -> IndexResult:
        self.calls.append({"index_name": index_name, "version": version})
        if self.error is not None:
            raise self.error
        return IndexResult(
            success=self.success,
            is_conflict=False,
            status_code=201 if self.success else 500,
            document_id=str(document["id"]),
            version=version,
        )


def _make_message(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "mail_account_id": uuid.uuid4(),
        "provider_message_id": "msg-1",
        "version": 3,
        "is_deleted": False,
        "subject": "Quarterly numbers",
        "body": {"contentType": "text", "content": "body"},
        "body_preview": "quarterly numbers attached",
        "sender": {"name": "Alice", "email": "Alice@Example.com"},
        "received_date_time": None,
        "has_attachments": False,
        "is_read": False,
    }
    defaults.update(overrides)
    return type("FakeMessage", (), defaults)()


def _make_event(message: Any) -> Any:
    return type(
        "FakeEvent",
        (),
        {
            "id": uuid.uuid4(),
            "tenant_id": message.tenant_id,
            "aggregate_id": message.id,
            "aggregate_version": message.version,
            "event_type": "MAIL_MESSAGE_MUTATED",
            "status": OutboxEventStatus.PENDING,
            "attempt_count": 0,
            "locked_by": None,
            "lease_version": 1,
        },
    )()


async def _async_return(value: Any) -> Any:
    return value


def _build_worker(
    *,
    session: RecordingSession,
    es_adapter: Any,
    arq_redis: Any,
    message: Any,
    event: Any,
) -> OutboxWorker:
    """Assemble an OutboxWorker with recording collaborators and no live ES."""
    worker = OutboxWorker(session, es_adapter=es_adapter, arq_redis=arq_redis)
    worker.index_provisioner = None
    repo = RecordingOutboxRepo(session, event)
    session.commit_hook = repo.flush

    async def _message_repo_get(_aggregate_id: Any) -> Any:
        return message

    worker.outbox_repo = repo  # type: ignore[assignment]
    worker.message_repo.get = _message_repo_get  # type: ignore[attr-defined]
    worker.participant_repo.get_by_message_id = (  # type: ignore[attr-defined]
        lambda _mid: _async_return([])
    )
    worker.pivot_repo.get_memberships_for_message = (  # type: ignore[attr-defined]
        lambda _mid: _async_return([])
    )
    return worker


def _persisted(worker: OutboxWorker) -> dict[str, Any]:
    return worker.outbox_repo.persisted  # type: ignore[attr-defined,no-any-return]


# ---------------------------------------------------------------------------
# M2 -- ordering invariant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_enqueue_precedes_the_durable_done_transition() -> None:
    """Required order: enqueue -> durable DONE -> commit.

    The previous code did DONE-CAS -> enqueue -> commit, holding a PostgreSQL
    write transaction open across the Redis await.
    """
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=ArqLikeQueue(log=session.log),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is True

    enqueue_at = session.log.index("enqueue")
    cas_done_at = session.log.index(f"cas:{OutboxEventStatus.DONE}")
    commit_at = session.log.index("commit")

    assert enqueue_at < cas_done_at < commit_at, session.log
    assert session.log[-1] == "commit", "the terminal commit must be last"
    assert _persisted(worker)["status"] == str(OutboxEventStatus.DONE)


@pytest.mark.asyncio
async def test_no_transaction_is_open_when_enqueueing() -> None:
    """M2: awaiting ARQ/Redis must not hold a PostgreSQL transaction open."""
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    queue = ArqLikeQueue()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=queue,
        message=message,
        event=event,
    )

    observed: list[bool] = []
    original = queue.enqueue_job

    async def _tracking(*args: Any, **kwargs: Any) -> Any:
        observed.append(session.in_transaction())
        return await original(*args, **kwargs)

    queue.enqueue_job = _tracking  # type: ignore[method-assign]

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is True

    assert observed == [False], "a transaction was open while enqueueing"
    assert worker.enqueue_transaction_violations == 0


@pytest.mark.asyncio
async def test_elasticsearch_call_also_happens_outside_a_transaction() -> None:
    """The existing NB-1 guarantee must survive the new ordering."""
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    adapter = ScriptedEsAdapter()
    observed: list[bool] = []
    original = adapter.index_message

    async def _tracking(*args: Any, **kwargs: Any) -> Any:
        observed.append(session.in_transaction())
        return await original(*args, **kwargs)

    adapter.index_message = _tracking  # type: ignore[method-assign]

    worker = _build_worker(
        session=session,
        es_adapter=adapter,
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )
    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is True

    assert observed == [False]


# ---------------------------------------------------------------------------
# M2 -- enqueue failure recovery
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_enqueue_failure_returns_event_to_pending_not_done() -> None:
    """A Redis outage must not permanently mark the event DONE.

    Regression: the old code committed the DONE transition from the error
    handler, because the uncommitted DONE CAS had already cleared ``locked_by``
    so the DEAD_LETTER CAS could not match. The message was then permanently
    unembedded, with no retry and no recorded error.
    """
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    queue = ArqLikeQueue()
    queue.fail_times = 1
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=queue,
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False

    assert f"cas:{OutboxEventStatus.DONE}" not in session.log
    assert f"cas:{OutboxEventStatus.DEAD_LETTER}" not in session.log
    assert _persisted(worker)["status"] == str(OutboxEventStatus.PENDING)
    assert queue.enqueued == [], "no job may be recorded when the enqueue failed"


@pytest.mark.asyncio
async def test_enqueue_failure_schedules_a_backoff_not_immediate_spin() -> None:
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    queue = ArqLikeQueue()
    queue.fail_times = 1
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=queue,
        message=message,
        event=event,
    )

    await worker.process_outbox_event(event.id, uuid.uuid4())
    persisted = _persisted(worker)

    assert persisted["next_attempt_at"] is not None
    assert persisted["next_attempt_at"] > datetime.now(UTC)
    assert "enqueue" in (persisted["last_error"] or "").lower()


@pytest.mark.asyncio
async def test_enqueue_failure_is_recoverable_on_retry() -> None:
    """Once the outage clears, the retry must reach DONE and enqueue the job."""
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    queue = ArqLikeQueue()
    queue.fail_times = 1
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=queue,
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.PENDING)

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is True
    assert _persisted(worker)["status"] == str(OutboxEventStatus.DONE)
    assert queue.enqueued[0][2] == embed_job_id(message.id, message.version)


# ---------------------------------------------------------------------------
# M2 -- crash between enqueue and commit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_crash_after_enqueue_before_commit_converges_to_done() -> None:
    """Crash window: the enqueue succeeded, the commit was lost.

    Required behaviour: the same logical job is retried, duplicate delivery is
    harmless, and the event eventually reaches DONE.
    """
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession(fail_commit_on=1)
    queue = ArqLikeQueue()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=queue,
        message=message,
        event=event,
    )

    # Run 1: the enqueue succeeds, then the durability boundary fails. A commit
    # failure propagates out of the job so ARQ sees a failed attempt and retries
    # it, rather than being swallowed into a state transition.
    with pytest.raises(RuntimeError, match="simulated crash during commit"):
        await worker.process_outbox_event(event.id, uuid.uuid4())

    assert queue.enqueued, "the enqueue happened before the crash"
    first_job_id = queue.enqueued[0][2]
    assert f"cas:{OutboxEventStatus.DONE}" in session.log
    assert "commit_failed" in session.log
    # The DONE transition was staged but never committed -> not durable.
    assert _persisted(worker) == {}

    # Run 2: ARQ retries the same logical job. The ES write is fenced by an
    # external version, so it is idempotent, and the enqueue carries the same
    # deterministic job id, so ARQ collapses it: no second embedding job exists
    # and no duplicate work is queued. The event converges to DONE regardless.
    session.fail_commit_on = None
    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is True

    assert len(queue.enqueued) == 1, "the retry enqueued duplicate embedding work"
    assert queue.enqueued[0][2] == first_job_id
    assert first_job_id in queue.live_jobs, "the embedding job must still be queued"
    assert _persisted(worker)["status"] == str(OutboxEventStatus.DONE)


@pytest.mark.asyncio
async def test_duplicate_embedding_delivery_is_deduplicated_by_job_id() -> None:
    """ARQ collapses the duplicate enqueue produced by the crash window."""
    message = _make_message()
    queue = ArqLikeQueue()
    job_id = embed_job_id(message.id, message.version)

    assert await queue.enqueue_job("embed_message_job", "m", _job_id=job_id) is not None
    # Same job still queued -> ARQ returns None.
    assert await queue.enqueue_job("embed_message_job", "m", _job_id=job_id) is None

    # Job finished: the job key is deleted but a result key now exists.
    queue.complete(job_id)
    assert await queue.enqueue_job("embed_message_job", "m", _job_id=job_id) is None

    # Result TTL elapsed: the key is gone and a re-enqueue is permitted. This is
    # correct, because a message version is immutable and can never require
    # re-embedding.
    queue.results.discard(job_id)
    assert await queue.enqueue_job("embed_message_job", "m", _job_id=job_id) is not None


@pytest.mark.asyncio
async def test_deleted_messages_skip_the_embedding_enqueue() -> None:
    message = _make_message(is_deleted=True)
    event = _make_event(message)
    session = RecordingSession()
    queue = ArqLikeQueue()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(),
        arq_redis=queue,
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is True
    assert queue.enqueued == []


# ---------------------------------------------------------------------------
# M3 -- error classification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_permanent_error_dead_letters_immediately() -> None:
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(PermanentElasticsearchError("bad mapping", status_code=400)),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.DEAD_LETTER)


@pytest.mark.asyncio
async def test_retryable_error_stays_pending() -> None:
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(RetryableElasticsearchError("503", status_code=503)),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.PENDING)


@pytest.mark.asyncio
async def test_unknown_error_does_not_dead_letter() -> None:
    """M3: a blanket ``except Exception`` used to dead-letter programming errors.

    A ``TypeError`` raised while building the document was classified
    identically to a genuine mapping conflict, and the event was abandoned
    permanently.
    """
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(TypeError("unhashable type: 'dict'")),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.PENDING)
    assert _persisted(worker)["status"] != str(OutboxEventStatus.DEAD_LETTER)
    assert "unclassified" in (_persisted(worker)["last_error"] or "")


@pytest.mark.asyncio
async def test_non_success_result_is_recoverable() -> None:
    """A non-success result must not leave the event dangling IN_FLIGHT."""
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(success=False),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.PENDING)


@pytest.mark.asyncio
async def test_exhausted_attempts_dead_letter() -> None:
    """Retryable errors dead-letter only once the attempt budget is spent."""
    message = _make_message()
    event = _make_event(message)
    event.attempt_count = 4  # the next attempt is #5 == max_attempts
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(RetryableElasticsearchError("503", status_code=503)),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.DEAD_LETTER)


@pytest.mark.asyncio
async def test_tenant_mismatch_is_dead_lettered() -> None:
    """Cross-tenant corruption must never be indexed under either tenant."""
    message = _make_message()
    event = _make_event(message)
    event.tenant_id = uuid.uuid4()  # deliberately different
    session = RecordingSession()
    adapter = ScriptedEsAdapter()
    worker = _build_worker(
        session=session,
        es_adapter=adapter,
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    assert await worker.process_outbox_event(event.id, uuid.uuid4()) is False
    assert _persisted(worker)["status"] == str(OutboxEventStatus.DEAD_LETTER)
    assert adapter.calls == [], "the document must not reach Elasticsearch"


@pytest.mark.asyncio
async def test_retry_after_is_honoured_for_backoff() -> None:
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(
            RetryableElasticsearchError("429", status_code=429, retry_after=120.0)
        ),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    await worker.process_outbox_event(event.id, uuid.uuid4())
    next_attempt = _persisted(worker)["next_attempt_at"]
    assert next_attempt is not None
    delay = (next_attempt - datetime.now(UTC)).total_seconds()
    assert 110 < delay <= 120, "Retry-After was not honoured"


@pytest.mark.asyncio
async def test_secrets_are_redacted_from_persisted_errors() -> None:
    """``outbox_events.last_error`` is durable and operator-visible."""
    message = _make_message()
    event = _make_event(message)
    session = RecordingSession()
    worker = _build_worker(
        session=session,
        es_adapter=ScriptedEsAdapter(
            PermanentElasticsearchError("auth failed: Bearer secret_token_xyz123")
        ),
        arq_redis=ArqLikeQueue(),
        message=message,
        event=event,
    )

    await worker.process_outbox_event(event.id, uuid.uuid4())
    last_error = _persisted(worker)["last_error"] or ""
    assert "secret_token_xyz123" not in last_error
    assert "[REDACTED_SECRET]" in last_error


# ---------------------------------------------------------------------------
# M1 -- duplicate scheduling identity
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repeated_polls_with_the_same_lease_version_collapse() -> None:
    """The poller's job id is (event, lease_version), so ticks dedupe."""
    event_id, lease_version = uuid.uuid4(), 3
    queue = ArqLikeQueue()
    job_id = outbox_job_id(event_id, lease_version)

    assert await queue.enqueue_job("process_outbox_event_job", "x", 3, _job_id=job_id)
    # Every subsequent 10-second tick produces the identical id.
    for _ in range(5):
        assert await queue.enqueue_job("process_outbox_event_job", "x", 3, _job_id=job_id) is None


@pytest.mark.asyncio
async def test_new_lease_version_produces_a_new_enqueue() -> None:
    """A genuine retry after a crash or lease expiry must not be suppressed."""
    event_id = uuid.uuid4()
    queue = ArqLikeQueue()

    assert (
        await queue.enqueue_job(
            "process_outbox_event_job", "x", 1, _job_id=outbox_job_id(event_id, 1)
        )
        is not None
    )
    # The lease expired and was re-claimed: lease_version advanced.
    assert (
        await queue.enqueue_job(
            "process_outbox_event_job", "x", 2, _job_id=outbox_job_id(event_id, 2)
        )
        is not None
    )


def test_job_ids_are_scoped_to_the_lease_version() -> None:
    event_id = uuid.uuid4()
    assert outbox_job_id(event_id, 1) == f"outbox:{event_id}:1"
    assert outbox_job_id(event_id, 1) != outbox_job_id(event_id, 2)


def test_poller_uses_deterministic_job_ids() -> None:
    """Static check that the poller actually passes ``_job_id``."""
    from mip_workers.worker import outbox_polling_cron

    source = inspect.getsource(outbox_polling_cron)
    assert "_job_id=outbox_job_id(event_id, lease_version)" in source
    assert "lease_versions" in source


def test_arq_dedup_contract_is_bounded_not_permanent() -> None:
    """Pin the verified ARQ semantics that justify the job id design.

    If a future ARQ upgrade changes this, the job id strategy must be revisited.
    """
    from arq.connections import ArqRedis
    from arq.worker import Worker

    enqueue_source = inspect.getsource(ArqRedis.enqueue_job)
    assert "job_id = _job_id or uuid4().hex" in enqueue_source
    assert "if await pipe.exists(job_key, result_key_prefix + job_id)" in enqueue_source
    assert "return None" in enqueue_source

    finish_source = inspect.getsource(Worker.finish_job)
    assert "result_key_prefix + job_id" in finish_source
    assert "job_key_prefix + job_id" in finish_source


# ---------------------------------------------------------------------------
# Live ARQ deduplication (service-backed; skips locally, fails in CI)
# ---------------------------------------------------------------------------
@pytest.fixture
async def live_redis():
    from redis import asyncio as aioredis

    client = aioredis.from_url(REDIS_TEST_URL, socket_connect_timeout=2)
    try:
        await client.ping()
    except Exception as err:
        await client.aclose()
        if CI:
            pytest.fail(f"CI requires Redis at {REDIS_TEST_URL}, but it failed: {err}")
        pytest.skip(f"Redis unavailable at {REDIS_TEST_URL}: {err}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_arq_deduplicates_deterministic_job_ids(live_redis) -> None:
    """Verify the M1 fix against real ARQ rather than a model of it."""
    from arq.connections import RedisSettings, create_pool

    arq_redis = await create_pool(RedisSettings.from_dsn(REDIS_TEST_URL))
    event_id, lease_version = uuid.uuid4(), 7
    job_id = outbox_job_id(event_id, lease_version)
    next_job_id = outbox_job_id(event_id, lease_version + 1)

    try:
        first = await arq_redis.enqueue_job("no_such_function", "a", _job_id=job_id)
        assert first is not None, "the first enqueue must be accepted"

        second = await arq_redis.enqueue_job("no_such_function", "a", _job_id=job_id)
        assert second is None, "ARQ must deduplicate the same job id"

        # A re-claimed event is a different logical claim.
        other = await arq_redis.enqueue_job("no_such_function", "a", _job_id=next_job_id)
        assert other is not None, "a re-claimed event must be enqueued"
    finally:
        for key in ("arq:job:" + job_id, "arq:job:" + next_job_id):
            await live_redis.delete(key)
        await arq_redis.aclose()
