"""Elasticsearch adapter for mail message document projection (PR-2.4).

Provides external-versioned indexing for canonical PostgreSQL mail messages.
Handles version conflicts (HTTP 409) as idempotent successes per the Mail Sync EDD.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ElasticsearchError(Exception):
    """Base error for Elasticsearch operations."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RetryableElasticsearchError(ElasticsearchError):
    """Transient Elasticsearch error suitable for exponential backoff retry."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class PermanentElasticsearchError(ElasticsearchError):
    """Unrecoverable Elasticsearch error triggering DEAD_LETTER transition."""


@dataclass(frozen=True)
class IndexResult:
    """Summary of an Elasticsearch document index request."""

    success: bool
    is_conflict: bool
    status_code: int
    document_id: str
    version: int


class ElasticsearchMailAdapter:
    """HTTP client adapter projecting canonical mail documents to Elasticsearch."""

    def __init__(
        self,
        base_url: str = "http://localhost:9200",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def index_message(
        self,
        index_name: str,
        document: dict[str, Any],
        version: int,
    ) -> IndexResult:
        """Index or update a message document using external versioning.

        Maps aggregate_version to Elasticsearch version.
        HTTP 409 Version Conflict (older or equal version) is returned as an idempotent success.
        """
        doc_id = str(document["id"])
        url = f"{self.base_url}/{index_name}/_doc/{doc_id}"
        params = {
            "version": str(version),
            "version_type": "external_gte",
        }

        own_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
            own_client = True

        try:
            response = await client.put(url, params=params, json=document)

            if response.status_code in (200, 201):
                return IndexResult(
                    success=True,
                    is_conflict=False,
                    status_code=response.status_code,
                    document_id=doc_id,
                    version=version,
                )

            if response.status_code == 409:
                body_text = response.text.lower()
                if (
                    "version_conflict_engine_exception" in body_text
                    or "version conflict" in body_text
                    or "version" in body_text
                    or not body_text
                ):
                    logger.info(
                        "ES external version conflict for doc %s version %s (treated as success)",
                        doc_id,
                        version,
                    )
                    return IndexResult(
                        success=True,
                        is_conflict=True,
                        status_code=409,
                        document_id=doc_id,
                        version=version,
                    )
                raise PermanentElasticsearchError(
                    f"Elasticsearch 409 Conflict (non-version): {response.text[:200]}"
                )

            retry_after: float | None = None
            retry_header = response.headers.get("Retry-After")
            if retry_header is not None:
                try:
                    retry_after = float(retry_header)
                except ValueError:
                    retry_after = None

            if response.status_code in (500, 502, 503, 504, 429):
                raise RetryableElasticsearchError(
                    f"Elasticsearch transient HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                    retry_after=retry_after,
                )

            raise PermanentElasticsearchError(
                f"Elasticsearch permanent HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableElasticsearchError(
                f"Elasticsearch network/timeout error: {exc}",
                status_code=None,
            ) from exc
        finally:
            if own_client:
                await client.aclose()

    async def update_embeddings(
        self,
        index_name: str,
        document_id: str,
        semantic_vector: list[float],
        embedding_model_id: str,
        version: int,
    ) -> bool:
        """Perform a scripted partial update to inject semantic vectors.

        Only updates if the requested version is >= the current document version.
        This provides idempotency and stale-job rejection.
        Returns True if successful, False if skipped due to version conflict or missing doc.
        """
        url = f"{self.base_url}/{index_name}/_update/{document_id}"

        # We use a painles script to only apply the vector if the embedding
        # is for the same (or newer) version of the document.
        body = {
            "script": {
                "source": """
                    if (ctx._source.version != null && ctx._source.version > params.version) {
                        ctx.op = 'none';
                    } else {
                        ctx._source.semantic_vector = params.semantic_vector;
                        ctx._source.embedding_model_id = params.embedding_model_id;
                        ctx._source.embedding_version = params.version;
                        ctx._source.embedding_dimensions = params.embedding_dimensions;
                    }
                """,
                "lang": "painless",
                "params": {
                    "semantic_vector": semantic_vector,
                    "embedding_model_id": embedding_model_id,
                    "version": version,
                    "embedding_dimensions": len(semantic_vector),
                },
            }
        }

        own_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
            own_client = True

        try:
            response = await client.post(url, json=body)

            if response.status_code in (200, 201):
                # We can check response.json().get("result")
                # If 'noop', then the script bypassed. We can treat both as success.
                return True

            if response.status_code == 404:
                # Document was deleted or hasn't arrived yet
                logger.warning(
                    "Doc %s not found for embedding update in %s", document_id, index_name
                )
                return False

            if response.status_code == 409:
                return False

            retry_after: float | None = None
            if "Retry-After" in response.headers:
                with contextlib.suppress(ValueError):
                    retry_after = float(response.headers["Retry-After"])

            if response.status_code in (500, 502, 503, 504, 429):
                raise RetryableElasticsearchError(
                    f"Elasticsearch transient HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                    retry_after=retry_after,
                )

            raise PermanentElasticsearchError(
                f"Elasticsearch permanent HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableElasticsearchError(
                f"Elasticsearch network/timeout error: {exc}",
                status_code=None,
            ) from exc
        finally:
            if own_client:
                await client.aclose()
