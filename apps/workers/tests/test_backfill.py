"""Tests for the EmbeddingBackfillWorker.

Uses mock embedding provider and mock ES adapter — no external services.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mip_ai.embeddings.base import EmbeddingResult
from mip_ai.embeddings.errors import EmbeddingPermanentError
from mip_models.mail import BackfillStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_provider(*, dims: int = 4) -> MagicMock:
    provider = MagicMock()
    provider.model_id = "test-model"
    provider.dimensions = dims

    async def embed(texts):
        vectors = [[0.1] * dims for _ in texts]
        return EmbeddingResult(
            vectors=vectors,
            model_id="test-model",
            dimensions=dims,
            total_tokens=len(texts) * 10,
            latency_ms=1.0,
            provider="mock",
        )

    provider.embed = embed
    return provider


def _make_mock_es_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.update_embeddings = AsyncMock(return_value=True)
    return adapter


def _make_message(
    msg_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    version: int = 1,
    subject: str = "Test",
    sender: str | dict | None = None,
    body_preview: str = "Hello world",
    is_deleted: bool = False,
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id or uuid.uuid4()
    msg.tenant_id = tenant_id or uuid.uuid4()
    msg.version = version
    msg.subject = subject
    msg.sender = sender
    msg.body_preview = body_preview
    msg.is_deleted = is_deleted
    return msg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_processes_all_messages():
    """Case 9: Full backfill of 3 messages → all embedded."""
    from mip_workers.backfill import EmbeddingBackfillWorker

    tenant_id = uuid.uuid4()
    messages = [_make_message(tenant_id=tenant_id) for _ in range(3)]

    provider = _make_mock_provider()
    es_adapter = _make_mock_es_adapter()

    session = AsyncMock()

    # Mock progress creation and fetch
    with (
        patch.object(
            EmbeddingBackfillWorker,
            "_get_or_create_progress",
        ) as mock_progress_fn,
        patch.object(
            EmbeddingBackfillWorker,
            "_fetch_batch",
            side_effect=[messages, []],
        ),
    ):
        progress = MagicMock()
        progress.status = BackfillStatus.PENDING
        progress.started_at = None
        progress.last_cursor_id = None
        progress.total_processed = 0
        progress.total_embedded = 0
        progress.total_skipped = 0
        progress.total_failed = 0
        progress.tenant_id = tenant_id
        progress.embedding_model = "test-model"
        mock_progress_fn.return_value = progress

        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )

        await worker.run(tenant_id)

    assert progress.total_processed == 3
    assert progress.total_embedded == 3
    assert progress.total_failed == 0
    assert es_adapter.update_embeddings.call_count == 3


@pytest.mark.asyncio
async def test_backfill_tenant_isolation():
    """Case 13: Backfill only processes target tenant."""
    from mip_workers.backfill import EmbeddingBackfillWorker

    tenant_a = uuid.uuid4()

    # Messages for tenant A only
    messages_a = [_make_message(tenant_id=tenant_a) for _ in range(2)]

    provider = _make_mock_provider()
    es_adapter = _make_mock_es_adapter()
    session = AsyncMock()

    with (
        patch.object(
            EmbeddingBackfillWorker,
            "_get_or_create_progress",
        ) as mock_progress_fn,
        patch.object(
            EmbeddingBackfillWorker,
            "_fetch_batch",
            side_effect=[messages_a, []],
        ),
    ):
        progress = MagicMock()
        progress.status = BackfillStatus.PENDING
        progress.started_at = None
        progress.last_cursor_id = None
        progress.total_processed = 0
        progress.total_embedded = 0
        progress.total_skipped = 0
        progress.total_failed = 0
        progress.tenant_id = tenant_a
        progress.embedding_model = "test-model"
        mock_progress_fn.return_value = progress

        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )
        await worker.run(tenant_a)

    # Verify all ES calls used tenant_a's index
    for call in es_adapter.update_embeddings.call_args_list:
        assert str(tenant_a) in call.kwargs.get("index_name", call.args[0] if call.args else "")


@pytest.mark.asyncio
async def test_backfill_permanent_provider_error():
    """Case 14: Permanent provider error → batch fails, progress preserved."""
    from mip_workers.backfill import EmbeddingBackfillWorker

    tenant_id = uuid.uuid4()
    messages = [_make_message(tenant_id=tenant_id) for _ in range(3)]

    provider = MagicMock()
    provider.model_id = "test-model"
    provider.dimensions = 4

    async def fail_embed(_texts):
        raise EmbeddingPermanentError("Invalid model")

    provider.embed = fail_embed

    es_adapter = _make_mock_es_adapter()
    session = AsyncMock()

    with (
        patch.object(
            EmbeddingBackfillWorker,
            "_get_or_create_progress",
        ) as mock_progress_fn,
        patch.object(
            EmbeddingBackfillWorker,
            "_fetch_batch",
            side_effect=[messages, []],
        ),
    ):
        progress = MagicMock()
        progress.status = BackfillStatus.PENDING
        progress.started_at = None
        progress.last_cursor_id = None
        progress.total_processed = 0
        progress.total_embedded = 0
        progress.total_skipped = 0
        progress.total_failed = 0
        progress.tenant_id = tenant_id
        progress.embedding_model = "test-model"
        mock_progress_fn.return_value = progress

        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )
        await worker.run(tenant_id)

    assert progress.total_failed == 3
    assert progress.total_embedded == 0
    # ES adapter should not have been called
    assert es_adapter.update_embeddings.call_count == 0


@pytest.mark.asyncio
async def test_backfill_es_failure_partial():
    """ES update failure for one doc → failure counted, others succeed."""
    from mip_workers.backfill import EmbeddingBackfillWorker

    tenant_id = uuid.uuid4()
    messages = [_make_message(tenant_id=tenant_id) for _ in range(3)]

    provider = _make_mock_provider()
    es_adapter = _make_mock_es_adapter()

    call_count = 0

    async def mock_update(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("ES down")
        return True

    es_adapter.update_embeddings = mock_update

    session = AsyncMock()

    with (
        patch.object(
            EmbeddingBackfillWorker,
            "_get_or_create_progress",
        ) as mock_progress_fn,
        patch.object(
            EmbeddingBackfillWorker,
            "_fetch_batch",
            side_effect=[messages, []],
        ),
    ):
        progress = MagicMock()
        progress.status = BackfillStatus.PENDING
        progress.started_at = None
        progress.last_cursor_id = None
        progress.total_processed = 0
        progress.total_embedded = 0
        progress.total_skipped = 0
        progress.total_failed = 0
        progress.tenant_id = tenant_id
        progress.embedding_model = "test-model"
        mock_progress_fn.return_value = progress

        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )
        await worker.run(tenant_id)

    assert progress.total_processed == 3
    assert progress.total_embedded == 2
    assert progress.total_failed == 1


@pytest.mark.asyncio
async def test_backfill_skips_completed():
    """Already-completed backfill returns immediately."""
    from mip_workers.backfill import EmbeddingBackfillWorker

    tenant_id = uuid.uuid4()

    provider = _make_mock_provider()
    es_adapter = _make_mock_es_adapter()
    session = AsyncMock()

    with patch.object(
        EmbeddingBackfillWorker,
        "_get_or_create_progress",
    ) as mock_progress_fn:
        progress = MagicMock()
        progress.status = BackfillStatus.COMPLETED
        progress.tenant_id = tenant_id
        progress.embedding_model = "test-model"
        progress.total_processed = 10
        progress.total_embedded = 10
        progress.total_skipped = 0
        progress.total_failed = 0
        progress.last_cursor_id = None
        mock_progress_fn.return_value = progress

        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )
        result = await worker.run(tenant_id)

    assert result["status"] == BackfillStatus.COMPLETED
    assert es_adapter.update_embeddings.call_count == 0


@pytest.mark.asyncio
async def test_backfill_stale_version_prevention() -> None:
    """Case 15: Late backfill v5 does not overwrite live v6 in ES."""
    from mip_workers.backfill import EmbeddingBackfillWorker

    tenant_id = uuid.uuid4()
    # Message in DB is version 5 (stale cursor snapshot)
    stale_msg = _make_message(tenant_id=tenant_id, version=5)

    provider = _make_mock_provider()
    es_adapter = MagicMock()

    # ES adapter update_embeddings receives version parameter
    # and returns False when version < doc version
    async def mock_update(
        index_name: str,
        document_id: str,
        semantic_vector: list[float],
        embedding_model_id: str,
        version: int,
    ) -> bool:
        # Simulate ES painless script: live doc is at version 6,
        # so version 5 update is a no-op / skipped
        return version >= 6

    es_adapter.update_embeddings = mock_update

    session = AsyncMock()

    with (
        patch.object(
            EmbeddingBackfillWorker,
            "_get_or_create_progress",
        ) as mock_progress_fn,
        patch.object(
            EmbeddingBackfillWorker,
            "_fetch_batch",
            side_effect=[[stale_msg], []],
        ),
    ):
        progress = MagicMock()
        progress.status = BackfillStatus.PENDING
        progress.started_at = None
        progress.last_cursor_id = None
        progress.total_processed = 0
        progress.total_embedded = 0
        progress.total_skipped = 0
        progress.total_failed = 0
        progress.tenant_id = tenant_id
        progress.embedding_model = "test-model"
        mock_progress_fn.return_value = progress

        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )
        await worker.run(tenant_id)

    # Stale version 5 message was skipped (total_skipped=1), live v6 was preserved
    assert progress.total_processed == 1
    assert progress.total_embedded == 0
    assert progress.total_skipped == 1
    assert progress.total_failed == 0
