import os
import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
from mip_workers.es_adapter import ElasticsearchMailAdapter

ES_TEST_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")


@pytest.fixture
async def es_clean_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    client = httpx.AsyncClient(base_url=ES_TEST_URL, timeout=5.0)
    try:
        resp = await client.get("/")
        if resp.status_code != 200:
            raise RuntimeError(f"ES ping status {resp.status_code}")
    except Exception as e:
        await client.aclose()
        if os.getenv("CI") == "true":
            pytest.fail(f"Elasticsearch required by CI is unavailable: {e}")
        pytest.skip(f"ES unavailable: {e}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def es_adapter() -> ElasticsearchMailAdapter:
    return ElasticsearchMailAdapter(ES_TEST_URL)


@pytest.mark.asyncio
async def test_es_version_fencing(
    es_clean_client: httpx.AsyncClient, es_adapter: ElasticsearchMailAdapter
) -> None:
    """Test full version fencing semantics (Cases A-G)."""
    tenant_id = uuid.uuid4()
    index_name = f"mail_messages_{tenant_id}"
    doc_id = "msg-fencing-test"

    # Clean index setup
    await es_clean_client.delete(f"/{index_name}", follow_redirects=True)
    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "subject": {"type": "text"},
                "body": {"type": "text"},
                "semantic_vector": {
                    "type": "dense_vector",
                    "dims": 1536,
                    "index": True,
                    "similarity": "cosine",
                },
                "embedding_model_id": {"type": "keyword"},
                "embedding_version": {"type": "long"},
                "is_deleted": {"type": "boolean"},
                "version": {"type": "long"},
            }
        }
    }
    res = await es_clean_client.put(f"/{index_name}", json=mapping)
    assert res.status_code == 200

    v1_vector = [0.1] * 1536
    v2_vector = [0.2] * 1536
    v3_vector = [0.3] * 1536
    v4_vector = [0.4] * 1536

    # -------------------------------------------------------------
    # Case A: V1 embedding -> indexed
    # -------------------------------------------------------------
    await es_adapter.index_message(
        index_name=index_name,
        document={
            "id": doc_id,
            "tenant_id": str(tenant_id),
            "subject": "Subject V1",
            "version": 1,
            "is_deleted": False,
        },
        version=1,
    )
    ok_v1 = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=v1_vector,
        embedding_model_id="test_model_v1",
        version=1,
    )
    assert ok_v1 is True
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["subject"] == "Subject V1"
    assert doc["semantic_vector"] == v1_vector
    assert doc["embedding_version"] == 1

    # -------------------------------------------------------------
    # Case B: V2 embedding -> indexed
    # -------------------------------------------------------------
    await es_adapter.index_message(
        index_name=index_name,
        document={
            "id": doc_id,
            "tenant_id": str(tenant_id),
            "subject": "Subject V2",
            "version": 2,
            "is_deleted": False,
        },
        version=2,
    )
    ok_v2 = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=v2_vector,
        embedding_model_id="test_model_v1",
        version=2,
    )
    assert ok_v2 is True
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["subject"] == "Subject V2"
    assert doc["semantic_vector"] == v2_vector
    assert doc["embedding_version"] == 2

    # -------------------------------------------------------------
    # Case C: late V1 embedding -> MUST NOT overwrite V2
    # -------------------------------------------------------------
    ok_late_v1 = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=v1_vector,
        embedding_model_id="test_model_v1",
        version=1,
    )
    assert ok_late_v1 is True
    # The update script noops when params.version < ctx._source.version
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["subject"] == "Subject V2"
    assert doc["semantic_vector"] == v2_vector
    assert doc["embedding_version"] == 2

    # -------------------------------------------------------------
    # Case D: tombstone V3 -> indexed as deleted
    # -------------------------------------------------------------
    await es_adapter.index_message(
        index_name=index_name,
        document={
            "id": doc_id,
            "tenant_id": str(tenant_id),
            "subject": "Subject V2",
            "version": 3,
            "is_deleted": True,
        },
        version=3,
    )
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["is_deleted"] is True
    assert doc["version"] == 3

    # -------------------------------------------------------------
    # Case E: late V2 embedding -> MUST NOT resurrect V3
    # -------------------------------------------------------------
    ok_late_v2 = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=v2_vector,
        embedding_model_id="test_model_v1",
        version=2,
    )
    assert ok_late_v2 is True
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["is_deleted"] is True
    assert doc["version"] == 3

    # -------------------------------------------------------------
    # Case F: duplicate V3 embedding -> idempotent
    # -------------------------------------------------------------
    ok_dup_v3 = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=v3_vector,
        embedding_model_id="test_model_v1",
        version=3,
    )
    assert ok_dup_v3 is True
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["is_deleted"] is True
    assert doc["version"] == 3

    # -------------------------------------------------------------
    # Case G: new V4 content/embedding -> allowed
    # -------------------------------------------------------------
    await es_adapter.index_message(
        index_name=index_name,
        document={
            "id": doc_id,
            "tenant_id": str(tenant_id),
            "subject": "Subject V4 Resurrected",
            "version": 4,
            "is_deleted": False,
        },
        version=4,
    )
    ok_v4 = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=v4_vector,
        embedding_model_id="test_model_v1",
        version=4,
    )
    assert ok_v4 is True
    await es_clean_client.post(f"/{index_name}/_refresh")

    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["is_deleted"] is False
    assert doc["subject"] == "Subject V4 Resurrected"
    assert doc["semantic_vector"] == v4_vector
    assert doc["embedding_version"] == 4

    # Cleanup
    await es_clean_client.delete(f"/{index_name}", follow_redirects=True)
