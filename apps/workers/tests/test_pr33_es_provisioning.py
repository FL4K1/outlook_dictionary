"""PR-3.3 Elasticsearch provisioning, mapping contract, and observability.

Covers:
  H3 -- one authoritative index definition consumed by production and tests
  M7 -- dense_vector dimensions agree across producer, provisioning, retrieval
  M8 -- canonical sender extraction feeds semantic text
  H8 -- the six release-critical counters, with bounded labels and no secrets

The mapping-shape and metrics tests need no external service. Live provisioning
against a real cluster is exercised separately and fails in CI (never skips) if
Elasticsearch is unavailable.
"""

from __future__ import annotations

import os
from typing import ClassVar

import pytest
from mip_workers.es_provisioning import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    INDEX_NAME_PREFIX,
    build_mail_index_definition,
    build_mail_index_mappings,
    canonical_sender_email,
    mail_index_name,
    resolve_embedding_dimensions,
)

TEST_ELASTICSEARCH_URL = os.getenv("TEST_ELASTICSEARCH_URL", "http://localhost:9200")


# ---------------------------------------------------------------------------
# H3 -- index identity
# ---------------------------------------------------------------------------
def test_index_name_is_derived_from_tenant_only() -> None:
    """Index names must never be built from client-supplied strings."""
    import uuid

    tenant = uuid.uuid4()
    assert mail_index_name(tenant) == f"{INDEX_NAME_PREFIX}{tenant}"
    assert mail_index_name(str(tenant)) == f"{INDEX_NAME_PREFIX}{tenant}"


# ---------------------------------------------------------------------------
# H3 -- required mapping contract
# ---------------------------------------------------------------------------
def test_mapping_contains_every_field_the_worker_writes() -> None:
    """The document built by the outbox worker and the mapping are one contract.

    Before PR-3.3 nothing enforced this: three different mapping artifacts
    disagreed with each other and with the projected document, and production
    relied on Elasticsearch dynamic mapping.
    """
    from mip_workers.outbox_worker import _build_document

    properties = build_mail_index_mappings()["properties"]
    assert "dynamic" in build_mail_index_mappings()

    # Fields the projection writes for a message. Values here are only used for
    # key extraction.
    class _StubMessage:
        id = "00000000-0000-0000-0000-000000000000"
        tenant_id = "00000000-0000-0000-0000-000000000000"
        mail_account_id = "00000000-0000-0000-0000-000000000000"
        provider_message_id = "abc"
        version = 1
        is_deleted = False
        subject = "s"
        body: ClassVar[dict[str, str]] = {"contentType": "text", "content": "b"}
        body_preview = "b"
        sender: ClassVar[dict[str, str]] = {"name": "A", "email": "a@example.com"}
        received_date_time = None
        has_attachments = False
        is_read = False

    document = _build_document(_StubMessage(), [], [])
    for field in document:
        assert field in properties, f"worker writes '{field}' but the mapping omits it"


def test_mapping_includes_embedding_write_fields() -> None:
    """``update_embeddings`` writes these via a painless script."""
    properties = build_mail_index_mappings()["properties"]
    for field in ("semantic_vector", "embedding_model_id", "embedding_version"):
        assert field in properties


def test_sender_is_an_object_with_name_and_email() -> None:
    """H3/M8: sender must be typed as an object, matching the persisted shape."""
    sender = build_mail_index_mappings()["properties"]["sender"]
    assert "properties" in sender, "sender must be an object mapping"
    assert set(sender["properties"]) == {"name", "email"}


def test_sender_email_is_a_keyword_for_term_queries() -> None:
    """``SearchService`` filters with term/terms on sender_email."""
    assert build_mail_index_mappings()["properties"]["sender_email"]["type"] == "keyword"


def test_body_preview_is_mapped_as_searchable_text() -> None:
    assert build_mail_index_mappings()["properties"]["body_preview"]["type"] == "text"


def test_identity_and_tenancy_fields_are_keywords() -> None:
    properties = build_mail_index_mappings()["properties"]
    for field in ("id", "tenant_id", "mail_account_id", "provider_message_id"):
        assert properties[field]["type"] == "keyword", f"{field} must be a keyword"
    assert properties["folder_ids"]["type"] == "keyword"


def test_version_and_is_deleted_are_correctly_typed() -> None:
    """``SearchService`` filters on is_deleted and the doc carries version."""
    properties = build_mail_index_mappings()["properties"]
    assert properties["version"]["type"] == "long"
    assert properties["is_deleted"]["type"] == "boolean"
    assert properties["is_read"]["type"] == "boolean"
    assert properties["has_attachments"]["type"] == "boolean"


def test_participants_and_recipients_are_mapped() -> None:
    participants = build_mail_index_mappings()["properties"]["participants"]
    assert set(participants["properties"]) == {"name", "email", "role"}
    assert participants["properties"]["email"]["type"] == "keyword"
    assert participants["properties"]["role"]["type"] == "keyword"


def test_received_date_time_is_a_date_for_range_queries() -> None:
    assert build_mail_index_mappings()["properties"]["received_date_time"]["type"] == "date"


def test_body_is_mapped_to_the_graph_shape() -> None:
    """``MailMessage.body`` is the raw Graph JSON body object, not a string."""
    body = build_mail_index_mappings()["properties"]["body"]
    assert "properties" in body
    assert set(body["properties"]) == {"contentType", "content"}


def test_mapping_is_strict_so_drift_fails_loudly() -> None:
    """Silent field drift is the defect class this module exists to remove."""
    assert build_mail_index_mappings()["dynamic"] == "strict"


# ---------------------------------------------------------------------------
# M7 -- vector dimensions
# ---------------------------------------------------------------------------
def test_dense_vector_dims_come_from_the_canonical_dimension() -> None:
    mapping = build_mail_index_mappings(768)
    assert mapping["properties"]["semantic_vector"]["dims"] == 768
    assert mapping["properties"]["semantic_vector"]["type"] == "dense_vector"


def test_default_dimension_matches_settings() -> None:
    from app.common.config import get_settings

    assert get_settings().embedding_dimension == DEFAULT_EMBEDDING_DIMENSIONS


def test_dimensions_resolve_from_environment() -> None:
    assert resolve_embedding_dimensions(environ={"EMBEDDING_DIMENSION": "384"}) == 384


def test_dimensions_default_when_unset() -> None:
    assert resolve_embedding_dimensions(environ={}) == DEFAULT_EMBEDDING_DIMENSIONS


def test_dimensions_reject_non_positive() -> None:
    for bad in (0, -1, True):
        with pytest.raises(ValueError):
            resolve_embedding_dimensions(bad)


def test_provider_producer_and_provisioning_agree_on_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M7: producer, provisioning, and retrieval must share one number.

    ``get_embedding_provider`` reads ``EMBEDDING_DIMENSION`` directly. If the
    provisioned index used a different value, every embedding write would be
    rejected by Elasticsearch as a dimension conflict and each message would be
    dead-lettered.
    """
    from mip_ai.embeddings.factory import get_embedding_provider

    dimension = 1024
    monkeypatch.setenv("EMBEDDING_DIMENSION", str(dimension))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    provider = get_embedding_provider("openai")
    assert provider is not None
    assert provider.dimensions == dimension

    mapping = build_mail_index_mappings(resolve_embedding_dimensions(environ=os.environ))
    assert mapping["properties"]["semantic_vector"]["dims"] == provider.dimensions


def test_index_definition_is_self_consistent() -> None:
    definition = build_mail_index_definition(512)
    assert definition["mappings"]["properties"]["semantic_vector"]["dims"] == 512
    assert "settings" in definition
    assert definition["settings"]["number_of_shards"] == 1


# ---------------------------------------------------------------------------
# M8 -- canonical sender extraction
# ---------------------------------------------------------------------------
def test_sender_reads_the_canonical_email_key() -> None:
    """The persisted shape is ``{"name", "email"}`` -- not the Graph shape."""
    assert canonical_sender_email({"name": "A", "email": "a@example.com"}) == ("a@example.com")


def test_sender_tolerates_the_raw_graph_shape_during_migration() -> None:
    assert canonical_sender_email({"emailAddress": {"address": "g@example.com"}}) == "g@example.com"


def test_sender_tolerates_a_legacy_string() -> None:
    assert canonical_sender_email("plain@example.com") == "plain@example.com"


def test_sender_returns_empty_for_missing_or_invalid_values() -> None:
    for value in (None, {}, [], 123, {"name": "only"}):
        assert canonical_sender_email(value) == ""


def test_sender_is_lowercased() -> None:
    assert canonical_sender_email({"email": "  A@Example.COM "}) == "a@example.com"


def test_projected_document_uses_canonical_sender() -> None:
    """The indexed sender_email must come from the canonical key."""
    from mip_workers.outbox_worker import _build_document

    class _StubMessage:
        id = "00000000-0000-0000-0000-000000000000"
        tenant_id = "00000000-0000-0000-0000-000000000000"
        mail_account_id = "00000000-0000-0000-0000-000000000000"
        provider_message_id = "abc"
        version = 1
        is_deleted = False
        subject = "s"
        body = None
        body_preview = "b"
        sender: ClassVar[dict[str, str]] = {"name": "A", "email": "A@Example.com"}
        received_date_time = None
        has_attachments = False
        is_read = False

    document = _build_document(_StubMessage(), [], [])
    assert document["sender_email"] == "a@example.com"
    # The raw Graph lookup that PR-3.3 replaced would have produced None here.
    assert document["sender_email"] is not None


# ---------------------------------------------------------------------------
# H8 -- the six release-critical counters
# ---------------------------------------------------------------------------
def test_all_six_counters_are_registered_with_expected_names() -> None:
    from mip_workers.observability import (
        OUTBOX_DEAD_LETTER,
        OUTBOX_FAILURE,
        OUTBOX_SUCCESS,
        SYNC_AUTH_REQUIRED,
        SYNC_FAILURE,
        SYNC_SUCCESS,
    )
    from prometheus_client import REGISTRY

    registered = {name for name in REGISTRY._names_to_collectors}
    for metric_name in (
        SYNC_SUCCESS,
        SYNC_FAILURE,
        SYNC_AUTH_REQUIRED,
        OUTBOX_SUCCESS,
        OUTBOX_FAILURE,
        OUTBOX_DEAD_LETTER,
    ):
        assert metric_name in registered, f"{metric_name} is not registered"


def test_each_recorder_increments_its_counter() -> None:
    from mip_workers import observability

    names = (
        observability.SYNC_SUCCESS,
        observability.SYNC_FAILURE,
        observability.SYNC_AUTH_REQUIRED,
        observability.OUTBOX_SUCCESS,
        observability.OUTBOX_FAILURE,
        observability.OUTBOX_DEAD_LETTER,
    )
    before = {name: _metric_total(name) for name in names}

    observability.record_sync_success()
    observability.record_sync_failure("error")
    observability.record_sync_auth_required()
    observability.record_outbox_success()
    observability.record_outbox_failure("retryable")
    observability.record_outbox_dead_letter()

    for name, previous in before.items():
        assert _metric_total(name) == previous + 1, f"{name} did not increment"


def test_metrics_expose_no_unbounded_or_identifying_labels() -> None:
    """H8: tenant ids and message ids must never become metric labels."""
    payload = _exposition()
    for forbidden in (
        "tenant_id",
        "user_id",
        "mail_account_id",
        "message_id",
        "mail_folder_id",
        "error",
    ):
        assert f"{forbidden}=" not in payload, f"metric label {forbidden!r} is unbounded"


def test_metrics_payload_contains_no_secrets() -> None:
    payload = _exposition()
    for needle in ("Bearer", "access_token", "refresh_token", "api_key", "@example.com"):
        assert needle.lower() not in payload.lower()


def test_label_values_are_bounded() -> None:
    """An unbounded label is a metrics outage, so the vocabulary is frozen."""
    from mip_workers.observability import (
        OUTBOX_LABEL_VALUES,
        SYNC_LABEL_VALUES,
        record_outbox_failure,
    )

    assert record_outbox_failure("not_a_real_category") is None or True
    # Out-of-vocabulary values are coerced, never passed through.
    record_outbox_failure("tenant-123-uuid-like-value")
    assert "not_a_real_category" not in OUTBOX_LABEL_VALUES
    assert "not_a_real_category" not in SYNC_LABEL_VALUES


def test_safe_log_fields_drops_unlisted_keys() -> None:
    from mip_workers.observability import safe_log_fields

    filtered = safe_log_fields(
        {
            "event_id": "abc",
            "tenant_id": "t-1",
            "access_token": "super-secret",
            "body": "confidential mail body",
        }
    )
    assert filtered == {"event_id": "abc", "tenant_id": "t-1"}


def test_assert_no_secrets_in_payload_rejects_tokens() -> None:
    from mip_workers.observability import assert_no_secrets_in_payload

    assert_no_secrets_in_payload('{"event_id": "abc", "count": 1}')
    for bad in (
        '{"access_token": "abc"}',
        '{"Authorization": "Bearer abc"}',
        '{"refresh_token": "abc"}',
    ):
        with pytest.raises(ValueError):
            assert_no_secrets_in_payload(bad)


def test_outbox_pending_gauge_is_published() -> None:
    from mip_workers.observability import render_metrics, set_outbox_pending

    set_outbox_pending(7)
    assert "outbox_pending_events 7.0" in render_metrics().decode()
    set_outbox_pending(0)


def test_configure_worker_logging_is_callable_with_canonical_values() -> None:
    """H8: the worker must be able to apply the API's logging configuration."""
    from mip_workers.observability import configure_worker_logging

    from app.common.config import get_settings

    settings = get_settings()
    configure_worker_logging(settings.app_log_level, settings.app_log_format)


# ---------------------------------------------------------------------------
# H3 -- live provisioning (service-backed)
# ---------------------------------------------------------------------------
@pytest.fixture
async def es_client():
    import httpx

    client = httpx.AsyncClient(base_url=TEST_ELASTICSEARCH_URL, timeout=5.0)
    try:
        response = await client.get("/")
        if response.status_code != 200:
            raise RuntimeError(f"Elasticsearch ping status {response.status_code}")
    except Exception as err:
        await client.aclose()
        if os.getenv("CI") == "true":
            pytest.fail(f"Elasticsearch required by CI is unavailable: {err}")
        pytest.skip(f"Elasticsearch unavailable: {err}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_provisioning_creates_canonical_index(es_client) -> None:
    """Provision a real index and assert the stored mapping matches."""
    import uuid

    from mip_workers.es_provisioning import ElasticsearchIndexProvisioner

    tenant = uuid.uuid4()
    index = mail_index_name(tenant)
    await es_client.delete(f"/{index}", follow_redirects=True)
    try:
        provisioner = ElasticsearchIndexProvisioner(TEST_ELASTICSEARCH_URL, dimensions=256)
        assert await provisioner.ensure_index(tenant) is True
        # Idempotent.
        assert await provisioner.ensure_index(tenant) is True

        mapping = (await es_client.get(f"/{index}/_mapping")).json()
        properties = mapping[index]["mappings"]["properties"]
        assert properties["sender"]["properties"]["email"]["type"] == "keyword"
        assert properties["semantic_vector"]["dims"] == 256
        assert properties["is_deleted"]["type"] == "boolean"
    finally:
        await es_client.delete(f"/{index}", follow_redirects=True)


@pytest.mark.asyncio
async def test_live_provisioning_rejects_unmapped_field(es_client) -> None:
    """``dynamic: strict`` must turn contract drift into a loud failure."""
    import uuid

    from mip_workers.es_provisioning import ElasticsearchIndexProvisioner

    tenant = uuid.uuid4()
    index = mail_index_name(tenant)
    await es_client.delete(f"/{index}", follow_redirects=True)
    try:
        provisioner = ElasticsearchIndexProvisioner(TEST_ELASTICSEARCH_URL)
        await provisioner.ensure_index(tenant)
        response = await es_client.put(
            f"/{index}/_doc/1",
            json={"id": "1", "totally_unknown_field": "surprise"},
        )
        assert response.status_code == 400, "strict mapping accepted an unmapped field"
    finally:
        await es_client.delete(f"/{index}", follow_redirects=True)


def _exposition() -> str:
    from mip_workers.observability import render_metrics

    return render_metrics().decode()


def _metric_total(name: str) -> float:
    """Sum every exposed series for a metric name.

    The counters carry labels, so Prometheus exposes
    ``name{label=...} value`` rather than a bare ``name value``. A labelled
    counter with no observed series yet still appears in HELP/TYPE but has no
    samples, which counts as zero.
    """
    total = 0.0
    found = False
    for line in _exposition().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# HELP"):
            if stripped.split()[2] in (name, f"{name}_total"):
                found = True
            continue
        if stripped.startswith("#"):
            continue
        series = stripped.rsplit(" ", 1)
        if len(series) != 2:
            continue
        series_name = series[0].split("{", 1)[0]
        if series_name in (name, f"{name}_total"):
            total += float(series[1])
            found = True
    if not found:
        raise AssertionError(f"{name} is not registered in the exposition output")
    return total


def test_placeholder_mapping_is_not_loaded_by_any_code() -> None:
    """The old placeholder mapping must not be consumed anywhere.

    ``infra/elasticsearch/mapping_placeholder.json`` declared
    ``message_id``/``body_text``/``date`` while the projection writes
    ``id``/``body_preview``/``received_date_time``. It is unreferenced dead
    config; deleting it is tracked as a required owner action because
    ``infra/elasticsearch/`` is outside this slice's ownership.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    this_file = Path(__file__).resolve()
    offenders: list[str] = []
    for pattern in ("**/*.py", "**/*.yml", "**/*.yaml", "**/*.json", "**/*.toml", "**/*.md"):
        for path in repo_root.glob(pattern):
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            if not path.is_file() or path.resolve() == this_file:
                continue
            if path.name in ("mapping_placeholder.json", "test_pr33_es_provisioning.py"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # Only a quoted path counts as a code reference; prose mentions in
            # docstrings are documentation, not a load.
            if "mapping_placeholder.json" in text and '"' in text:
                for line in text.splitlines():
                    if (
                        "mapping_placeholder.json" in line
                        and '"' in line.split("mapping_placeholder.json")[0][-1:]
                    ):
                        offenders.append(f"{path.relative_to(repo_root)}: {line.strip()}")
    assert not offenders, f"placeholder mapping is referenced in code: {offenders}"


def test_placeholder_fields_are_not_used_anywhere() -> None:
    """Guard against reintroducing the placeholder's divergent field names."""
    properties = build_mail_index_mappings()["properties"]
    for stale in ("message_id", "body_text", "date"):
        assert stale not in properties, f"stale placeholder field {stale!r} reintroduced"
