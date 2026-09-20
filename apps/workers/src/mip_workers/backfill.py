"""Resumable embedding backfill worker (PR-2.9).

Processes existing canonical mail messages that lack semantic vectors,
generating embeddings and writing them to Elasticsearch via the
version-fenced ``update_embeddings`` adapter.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from mip_ai.embeddings.errors import (
    EmbeddingPermanentError,
    EmbeddingTransientError,
)
from mip_ai.embeddings.text import build_semantic_text
from mip_models.mail import BackfillStatus, EmbeddingBackfillProgress, MailMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from mip_ai.embeddings.base import EmbeddingProvider
    from mip_workers.es_adapter import ElasticsearchMailAdapter

logger = logging.getLogger(__name__)

# Default batch size — kept small to limit memory and provider payload
DEFAULT_BATCH_SIZE = 50


class EmbeddingBackfillWorker:
    """Tenant-scoped, resumable, version-fenced embedding backfill.

    Uses keyset pagination on ``MailMessage.id`` (UUID, ordered)
    with a durable PostgreSQL checkpoint.
    """

    def __init__(
        self,
        session: AsyncSession,
        es_adapter: ElasticsearchMailAdapter,
        embedding_provider: EmbeddingProvider,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.session = session
        self.es_adapter = es_adapter
        self.provider = embedding_provider
        self.batch_size = batch_size

    async def run(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Execute a full backfill for *tenant_id*, resuming from checkpoint."""
        progress = await self._get_or_create_progress(tenant_id)

        if progress.status == BackfillStatus.COMPLETED:
            logger.info("Backfill already completed for tenant %s", tenant_id)
            return self._summary(progress)

        if progress.status == BackfillStatus.CANCELLED:
            logger.info("Backfill cancelled for tenant %s — skipping", tenant_id)
            return self._summary(progress)

        # Mark in-progress
        progress.status = BackfillStatus.IN_PROGRESS
        progress.started_at = progress.started_at or datetime.now(UTC)
        await self.session.commit()

        cursor = progress.last_cursor_id

        try:
            while True:
                batch = await self._fetch_batch(tenant_id, cursor)
                if not batch:
                    break

                await self._process_batch(batch, tenant_id, progress)

                cursor = batch[-1].id
                progress.last_cursor_id = cursor
                await self.session.commit()

                # Refresh progress — check for cancellation
                await self.session.refresh(progress)
                if progress.status == BackfillStatus.CANCELLED:
                    logger.info("Backfill cancelled mid-run for %s", tenant_id)
                    return self._summary(progress)

            progress.status = BackfillStatus.COMPLETED
            progress.completed_at = datetime.now(UTC)
            await self.session.commit()

        except Exception as exc:
            logger.error("Backfill failed for tenant %s: %s", tenant_id, exc)
            progress.status = BackfillStatus.FAILED
            progress.error_message = str(exc)[:2000]
            await self.session.commit()
            raise

        logger.info(
            "Backfill completed for tenant %s: %d processed, %d embedded, %d skipped, %d failed",
            tenant_id,
            progress.total_processed,
            progress.total_embedded,
            progress.total_skipped,
            progress.total_failed,
        )
        return self._summary(progress)

    # ---- internals ----

    async def _get_or_create_progress(self, tenant_id: uuid.UUID) -> EmbeddingBackfillProgress:
        stmt = select(EmbeddingBackfillProgress).where(
            EmbeddingBackfillProgress.tenant_id == tenant_id,
            EmbeddingBackfillProgress.embedding_model == self.provider.model_id,
        )
        result = await self.session.execute(stmt)
        progress = result.scalar_one_or_none()

        if progress is None:
            progress = EmbeddingBackfillProgress(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                embedding_model=self.provider.model_id,
                embedding_dims=self.provider.dimensions,
            )
            self.session.add(progress)
            await self.session.commit()

        return progress

    async def _fetch_batch(
        self,
        tenant_id: uuid.UUID,
        cursor: uuid.UUID | None,
    ) -> list[MailMessage]:
        stmt = (
            select(MailMessage)
            .where(
                MailMessage.tenant_id == tenant_id,
                MailMessage.is_deleted.is_(False),
            )
            .order_by(MailMessage.id.asc())
            .limit(self.batch_size)
        )
        if cursor is not None:
            stmt = stmt.where(MailMessage.id > cursor)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _process_batch(
        self,
        messages: list[MailMessage],
        tenant_id: uuid.UUID,
        progress: EmbeddingBackfillProgress,
    ) -> None:
        # Build texts
        texts: list[str] = []
        for msg in messages:
            sender_str = ""
            if isinstance(msg.sender, dict):
                sender_str = str(msg.sender.get("emailAddress", {}).get("address", ""))
            elif isinstance(msg.sender, str):
                sender_str = msg.sender

            texts.append(
                build_semantic_text(
                    subject=msg.subject or "",
                    sender=sender_str,
                    body=msg.body_preview or "",
                )
            )

        # Call provider
        try:
            embed_result = await self.provider.embed(texts)
        except EmbeddingTransientError:
            raise  # bubble up for retry
        except EmbeddingPermanentError as exc:
            logger.error("Permanent embedding error in backfill batch: %s", exc)
            progress.total_failed += len(messages)
            progress.total_processed += len(messages)
            return

        # Write vectors to ES
        index_name = f"mail_messages_{tenant_id}"
        for i, msg in enumerate(messages):
            progress.total_processed += 1

            if i >= len(embed_result.vectors):
                progress.total_failed += 1
                continue

            vector = embed_result.vectors[i]
            try:
                ok = await self.es_adapter.update_embeddings(
                    index_name=index_name,
                    document_id=str(msg.id),
                    semantic_vector=vector,
                    embedding_model_id=self.provider.model_id,
                    version=msg.version,
                )
                if ok:
                    progress.total_embedded += 1
                else:
                    progress.total_skipped += 1
            except Exception as exc:
                logger.warning(
                    "ES update failed for %s during backfill: %s",
                    msg.id,
                    exc,
                )
                progress.total_failed += 1

    @staticmethod
    def _summary(progress: EmbeddingBackfillProgress) -> dict[str, Any]:
        return {
            "tenant_id": str(progress.tenant_id),
            "status": progress.status,
            "total_processed": progress.total_processed,
            "total_embedded": progress.total_embedded,
            "total_skipped": progress.total_skipped,
            "total_failed": progress.total_failed,
            "embedding_model": progress.embedding_model,
            "last_cursor_id": str(progress.last_cursor_id) if progress.last_cursor_id else None,
        }


async def backfill_tenant_embeddings_job(
    ctx: dict[str, Any],
    tenant_id_str: str,
) -> dict[str, Any]:
    """ARQ job to backfill embeddings for a single tenant."""
    sessionmaker = ctx["sessionmaker"]
    es_adapter = ctx["es_adapter"]
    provider = ctx.get("embedding_provider")

    if not provider:
        logger.error("No embedding_provider for backfill job.")
        return {"error": "no_provider"}

    tenant_id = uuid.UUID(tenant_id_str)

    async with sessionmaker() as session:
        worker = EmbeddingBackfillWorker(
            session=session,
            es_adapter=es_adapter,
            embedding_provider=provider,
        )
        return await worker.run(tenant_id)
