"""Read-only Elasticsearch adapter for Mail Search API (PR-2.7)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SearchAdapterError(Exception):
    """Base error for Search operations."""


class SearchServiceUnavailableError(SearchAdapterError):
    """Raised when Elasticsearch is out of reach or fully unavailable (500/503)."""


class SearchInvalidQueryError(SearchAdapterError):
    """Raised when Elasticsearch rejects the query (e.g. 400)."""


class ElasticsearchSearchAdapter:
    """HTTP client adapter dedicated to read-only search operations."""

    def __init__(
        self,
        base_url: str = "http://localhost:9200",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def search(self, index_name: str, query: dict[str, Any]) -> dict[str, Any]:
        """Execute a read-only search request.

        Raises:
            SearchServiceUnavailableError: On transient/network or 5xx issues.
            SearchInvalidQueryError: On 400 query violations.
        """
        url = f"{self.base_url}/{index_name}/_search"
        client = self._client
        own_client = False

        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
            own_client = True

        try:
            # We enforce a hard timeout on the payload logic via ES too
            if "timeout" not in query:
                query["timeout"] = "3s"

            response = await client.post(url, json=query)

            if response.status_code == 200:
                body = response.json()
                return body  # type: ignore[no-any-return]

            if response.status_code == 400:
                logger.warning("ES 400 Invalid Query: %s", response.text[:200])
                raise SearchInvalidQueryError("The search query was rejected.")

            logger.error("ES unexpected HTTP %s: %s", response.status_code, response.text[:200])
            raise SearchServiceUnavailableError("Elasticsearch is currently unavailable.")

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.error("ES network/timeout error during search: %s", str(exc))
            raise SearchServiceUnavailableError(
                "Elasticsearch is unavailable (network error)."
            ) from exc
        finally:
            if own_client and client:
                await client.aclose()
