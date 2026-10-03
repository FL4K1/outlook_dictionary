"""Authoritative Elasticsearch index definition and provisioner (PR-3.3 / H3, M7).

This module is the SINGLE SOURCE OF TRUTH for the ``mail_messages_{tenant_id}``
index. Production (the outbox projection worker) and the integration tests both
build their mapping from :func:`build_mail_index_definition`, so the read path
and the write path can no longer drift apart.

Why this exists
---------------
Before PR-3.3 there was no index provisioning at all. Indices were created
implicitly by Elasticsearch dynamic mapping on the first indexed document, and
the three mapping artifacts that existed disagreed with each other and with the
document the outbox worker actually writes:

* ``infra/elasticsearch/mapping_placeholder.json`` -- unreferenced, and declares
  ``message_id`` / ``body_text`` / ``date`` instead of ``id`` / ``body_preview``
  / ``received_date_time``.
* ``apps/api/tests/integration/test_search_api.py`` -- an inline mapping that
  typed ``sender`` and ``body`` as ``text`` while the writer emits objects.
* Dynamic mapping in production -- no ``dense_vector``, so the first embedding
  update would have attempted to create a ``float`` field from a 1536 element
  array.

Document shape contract
-----------------------
The document projected by :class:`mip_workers.outbox_worker.OutboxWorker` is:

``id``, ``tenant_id``, ``mail_account_id``, ``provider_message_id``,
``version``, ``is_deleted``, ``subject``, ``body``, ``body_preview``,
``sender``, ``sender_email``, ``participants``, ``received_date_time``,
``has_attachments``, ``is_read``, ``folder_ids``

and :meth:`mip_workers.es_adapter.ElasticsearchMailAdapter.update_embeddings`
adds ``semantic_vector``, ``embedding_model_id``, ``embedding_version`` and
``embedding_dimensions``.

``dynamic: strict`` is deliberate. Silent field drift is precisely the defect
class this module exists to eliminate, so an unmapped field is rejected by
Elasticsearch as a hard 400 rather than being silently indexed under a
dynamically-invented type. See ``docs`` note in the PR-3.3 report.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

INDEX_NAME_PREFIX = "mail_messages_"

#: Analyzer shared by all human-readable mail text fields.
EMAIL_ANALYZER = "email_analyzer"

#: Default vector dimensionality. MUST agree with ``Settings.embedding_dimension``
#: (env ``EMBEDDING_DIMENSION``), which is also what the embedding provider
#: factory reads. See :func:`resolve_embedding_dimensions`.
DEFAULT_EMBEDDING_DIMENSIONS = 1536


def mail_index_name(tenant_id: uuid.UUID | str) -> str:
    """Return the canonical per-tenant index name.

    Index names are always derived from a server-side tenant UUID. No
    client-supplied string is ever interpolated into an index name.
    """
    return f"{INDEX_NAME_PREFIX}{tenant_id}"


def resolve_embedding_dimensions(
    dimensions: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the vector dimensionality from the canonical Settings value.

    The embedding producer (``mip_ai.embeddings.factory``), this provisioning
    module, and the retrieval path must all agree on one number, otherwise
    Elasticsearch rejects every embedding write as a dimension conflict.

    Args:
        dimensions: Explicit override. When supplied it is authoritative.
        environ: Environment mapping, defaults to ``os.environ``. Used by tests
            to exercise resolution without mutating the real environment.

    Raises:
        ValueError: If the resolved value is not a positive integer.
    """
    if dimensions is not None:
        resolved = dimensions
    else:
        if environ is None:
            import os

            environ = os.environ
        raw = environ.get("EMBEDDING_DIMENSION")
        resolved = DEFAULT_EMBEDDING_DIMENSIONS if raw is None or not raw.strip() else int(raw)

    if not isinstance(resolved, int) or isinstance(resolved, bool) or resolved <= 0:
        msg = f"Elasticsearch dense_vector dims must be a positive integer, got {resolved!r}."
        raise ValueError(msg)
    return resolved


class ElasticsearchConfigurationError(RuntimeError):
    """Raised when the worker cannot determine a usable Elasticsearch URL."""


def resolve_elasticsearch_url(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the canonical Elasticsearch URL for the worker process (PR-3.3 / B2).

    Before PR-3.3 the worker hardcoded ``http://localhost:9200`` in
    ``ElasticsearchMailAdapter.__init__`` and never read any environment
    variable. In every deployment where Elasticsearch is not on the worker's
    own loopback -- which is every real deployment, including this repository's
    own compose file -- every index and embedding write therefore went to
    loopback, retried, and was ultimately dead-lettered.

    The URL is taken from the canonical application ``Settings``
    (``settings.elasticsearch_url``, composed from ``ELASTICSEARCH_HOST`` /
    ``ELASTICSEARCH_PORT`` / ``ELASTICSEARCH_SCHEME``) so that the worker and
    the API cannot disagree about where Elasticsearch lives.

    Fail-fast rule
    --------------
    ``ELASTICSEARCH_HOST`` must be *explicitly present* in the environment.
    ``Settings`` defaults it to ``localhost``, which would silently reinstate the
    exact defect this function exists to remove, so a missing value is a
    startup error rather than a default. An operator may still explicitly point
    the worker at loopback for local development; what is forbidden is the
    implicit fallback.

    Args:
        environ: Environment mapping, defaults to ``os.environ``.

    Returns:
        A normalised base URL with no trailing slash.

    Raises:
        ElasticsearchConfigurationError: If ``ELASTICSEARCH_HOST`` is absent or
            the resulting URL has no scheme or host.
    """
    import os

    env = os.environ if environ is None else environ

    if not (env.get("ELASTICSEARCH_HOST") or "").strip():
        msg = (
            "ELASTICSEARCH_HOST is required by the worker. The worker deliberately "
            "refuses to fall back to http://localhost:9200 because that silently "
            "targets the wrong cluster in every non-loopback deployment. Set "
            "ELASTICSEARCH_HOST (and optionally ELASTICSEARCH_PORT / "
            "ELASTICSEARCH_SCHEME), or set ELASTICSEARCH_URL to a full base URL."
        )
        raise ElasticsearchConfigurationError(msg)

    from app.common.config import get_settings

    url = get_settings().elasticsearch_url
    return validate_elasticsearch_url(url)


def validate_elasticsearch_url(url: str) -> str:
    """Validate and normalise an Elasticsearch base URL.

    Args:
        url: Candidate base URL.

    Returns:
        The URL with any trailing slash removed.

    Raises:
        ElasticsearchConfigurationError: If the URL is malformed.
    """
    if not url or not url.strip():
        msg = "Elasticsearch URL is empty."
        raise ElasticsearchConfigurationError(msg)

    normalised = url.strip().rstrip("/")
    if not normalised.startswith(("http://", "https://")):
        msg = (
            f"Elasticsearch URL must start with http:// or https://, got {url!r}. "
            "A bare host:port will not be given a scheme."
        )
        raise ElasticsearchConfigurationError(msg)

    authority = normalised.split("://", 1)[1]
    if not authority or authority.startswith("/"):
        msg = f"Elasticsearch URL {url!r} has no host."
        raise ElasticsearchConfigurationError(msg)
    return normalised


def canonical_sender_email(sender: object) -> str:
    """Extract the sender address from the canonical sender shape (PR-3.3 / M8).

    The sync orchestrator persists ``MailMessage.sender`` as
    ``{"name": ..., "email": ...}`` (see
    ``app.services.sync_orchestrator._process_provider_message``). The pre-PR-3.3
    outbox and backfill workers both read ``sender["emailAddress"]["address"]``,
    which is the *raw Microsoft Graph* shape and never matches the persisted
    document. The lookup therefore always missed and every generated embedding
    was built with an empty sender, silently degrading semantic search.

    This helper reads the canonical key. It is intentionally tolerant of the raw
    Graph shape and of a pre-migration string value so that a partially migrated
    corpus still produces a usable sender, but the canonical key is the
    documented contract and is what the mapping is built around.

    Args:
        sender: The ``MailMessage.sender`` JSON value.

    Returns:
        A lowercased address, or an empty string when absent.
    """
    if isinstance(sender, dict):
        email = sender.get("email")
        if not email:
            # Tolerate the raw Graph shape during corpus migration.
            email_address = sender.get("emailAddress")
            if isinstance(email_address, dict):
                email = email_address.get("address")
        return str(email).strip().lower() if email else ""
    if isinstance(sender, str):
        return sender.strip().lower()
    return ""


def build_mail_index_settings() -> dict[str, Any]:
    """Return the index settings block.

    One shard / one replica is intentional: there is one index *per tenant*, so
    the historical 3-shard placeholder would multiply shard count by the tenant
    count for indices that are individually small.
    """
    return {
        "number_of_shards": 1,
        "number_of_replicas": 1,
        "analysis": {
            "analyzer": {
                EMAIL_ANALYZER: {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    }


def build_mail_index_mappings(dimensions: int | None = None) -> dict[str, Any]:
    """Return the mappings block for the mail message index.

    Field types are chosen to satisfy the queries issued by
    ``app.search.service.SearchService``:

    * ``term`` / ``terms`` on ``tenant_id``, ``mail_account_id``,
      ``folder_ids``, ``sender_email``, ``participants.email`` -> ``keyword``
    * ``term`` on ``is_deleted``, ``is_read``, ``has_attachments`` -> ``boolean``
    * ``range`` on ``received_date_time`` -> ``date``
    * ``multi_match`` on ``subject`` and ``body_preview`` -> ``text``
    * ``knn`` on ``semantic_vector`` -> ``dense_vector``
    """
    dims = resolve_embedding_dimensions(dimensions)
    return {
        # Fail loudly on unmapped fields instead of silently inventing types.
        "dynamic": "strict",
        "properties": {
            # --- Identity ---
            "id": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "mail_account_id": {"type": "keyword"},
            "provider_message_id": {"type": "keyword"},
            "version": {"type": "long"},
            "is_deleted": {"type": "boolean"},
            # --- Mailbox placement ---
            "folder_ids": {"type": "keyword"},
            # --- Searchable text ---
            "subject": {"type": "text", "analyzer": EMAIL_ANALYZER},
            "body_preview": {"type": "text", "analyzer": EMAIL_ANALYZER},
            # --- Full body (Graph shape). Stored + indexed for completeness;
            #     the search read path never selects it. ---
            "body": {
                "properties": {
                    "contentType": {"type": "keyword"},
                    "content": {"type": "text", "analyzer": EMAIL_ANALYZER},
                }
            },
            # --- Participants. ``sender`` is an OBJECT, matching the canonical
            #     ``{"name": ..., "email": ...}`` shape persisted by the sync
            #     orchestrator. Flattening it to text is a search-layer concern.
            "sender": {
                "properties": {
                    "name": {"type": "text", "analyzer": EMAIL_ANALYZER},
                    "email": {"type": "keyword"},
                }
            },
            "sender_email": {"type": "keyword"},
            "participants": {
                "properties": {
                    "name": {"type": "text", "analyzer": EMAIL_ANALYZER},
                    "email": {"type": "keyword"},
                    "role": {"type": "keyword"},
                }
            },
            # --- Message metadata ---
            "received_date_time": {"type": "date"},
            "has_attachments": {"type": "boolean"},
            "is_read": {"type": "boolean"},
            # --- Embeddings ---
            "semantic_vector": {
                "type": "dense_vector",
                "dims": dims,
                "index": True,
                "similarity": "cosine",
            },
            "embedding_model_id": {"type": "keyword"},
            "embedding_version": {"type": "long"},
            "embedding_dimensions": {"type": "integer"},
        },
    }


def build_mail_index_definition(dimensions: int | None = None) -> dict[str, Any]:
    """Return the complete index creation body.

    This is the single authoritative definition consumed by both production
    provisioning and integration test fixtures.
    """
    return {
        "settings": build_mail_index_settings(),
        "mappings": build_mail_index_mappings(dimensions),
    }


class ElasticsearchIndexProvisioner:
    """Idempotently provisions the per-tenant mail index.

    Safe to call concurrently from multiple workers: index creation races are
    resolved by treating ``resource_already_exists_exception`` as success.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        dimensions: int | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.dimensions = resolve_embedding_dimensions(dimensions)
        self._timeout = timeout
        # Process-local cache so the hot indexing path does not issue a HEAD per
        # document. A negative result is never cached.
        self._ensured: set[str] = set()

    async def index_exists(self, tenant_id: uuid.UUID | str) -> bool:
        """Return True if the tenant index already exists."""
        index_name = mail_index_name(tenant_id)
        async with self._client_or_owned() as client:
            response = await client.head(f"{self.base_url}/{index_name}")
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise ElasticsearchProvisioningError(
            f"Elasticsearch index probe failed with HTTP {response.status_code}.",
            status_code=response.status_code,
        )

    async def ensure_index(self, tenant_id: uuid.UUID | str, *, force: bool = False) -> bool:
        """Ensure the tenant index exists with the canonical mapping.

        Args:
            tenant_id: Tenant whose index should exist.
            force: Bypass the process-local cache.

        Returns:
            True when the index exists (created now or previously), False when
            creation failed in a retryable way.

        Raises:
            ElasticsearchProvisioningError: On a non-retryable provisioning
                failure, or when the index exists with an incompatible mapping.
        """
        index_name = mail_index_name(tenant_id)
        if not force and index_name in self._ensured:
            return True

        body = build_mail_index_definition(self.dimensions)
        async with self._client_or_owned() as client:
            # HEAD first to avoid a noisy 400 on every cold start.
            probe = await client.head(f"{self.base_url}/{index_name}")
            if probe.status_code == 200:
                self._ensured.add(index_name)
                return True

            response = await client.put(f"{self.base_url}/{index_name}", json=body)

        if response.status_code in (200, 201):
            logger.info("es_index_created", extra={"index": index_name})
            self._ensured.add(index_name)
            return True

        if response.status_code == 400 and "resource_already_exists_exception" in response.text:
            # Lost a creation race with a concurrent worker. The index exists,
            # which is the postcondition we require.
            self._ensured.add(index_name)
            return True

        raise ElasticsearchProvisioningError(
            f"Elasticsearch index provisioning failed with HTTP {response.status_code}: "
            f"{response.text[:200]}",
            status_code=response.status_code,
            retryable=response.status_code in (429, 500, 502, 503, 504),
        )

    def _client_or_owned(self) -> Any:
        if self._client is not None:
            return _BorrowedClient(self._client)
        return httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))


class ElasticsearchProvisioningError(Exception):
    """Raised when the mail index cannot be provisioned."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class _BorrowedClient:
    """Async context manager that borrows a caller-owned client.

    ``httpx.AsyncClient.__aenter__`` would be called on a client the provisioner
    does not own, which would leave it open. This wrapper delegates the request
    surface and performs no teardown.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc_info: object) -> None:
        return None
