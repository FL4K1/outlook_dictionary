import pytest
import os
import httpx
import uuid
from mip_workers.es_adapter import ElasticsearchMailAdapter
import asyncio

ES_TEST_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")

@pytest.fixture
async def es_clean_client():
    client = httpx.AsyncClient(base_url=ES_TEST_URL, timeout=5.0)
    try:
        resp = await client.get("/")
        if resp.status_code != 200:
            raise RuntimeError(f"ES ping status {resp.status_code}")
    except Exception as e:
        await client.aclose()
        pytest.skip(f"ES unavailable: {e}")
    yield client
    await client.aclose()


@pytest.fixture
def es_adapter():
    return ElasticsearchMailAdapter(ES_TEST_URL)


@pytest.mark.asyncio
async def test_es_version_fencing(es_clean_client, es_adapter):
    tenant_id = uuid.uuid4()
    index_name = f"mail_messages_{tenant_id}"
    doc_id = "msg-123"

    # Clean index
    await es_clean_client.delete(f"/{index_name}", follow_redirects=True)
    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "subject": {"type": "text"},
                "semantic_vector": {
                    "type": "dense_vector",
                    "dims": 1536,
                    "index": True,
                    "similarity": "cosine"
                },
                "is_deleted": {"type": "boolean"},
                "version": {"type": "long"}
            }
        }
    }
    res = await es_clean_client.put(f"/{index_name}", json=mapping)
    assert res.status_code == 200

    # 1. Canonical index mapping Version 2 first
    await es_adapter.index_message(
        index_name=index_name,
        document={"id": doc_id, "tenant_id": str(tenant_id), "subject": "V2 Subject", "version": 2, "is_deleted": False},
        version=2
    )

    await es_clean_client.post(f"/{index_name}/_refresh")

    # 2. Try to update embedding utilizing an older VERSION 1 mapping
    vector = [0.1] * 1536
    success = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=vector,
        embedding_model_id="model_id",
        version=1
    )
    
    # Should say True or run silently (noop) but not actually update doc version metadata to V1
    await es_clean_client.post(f"/{index_name}/_refresh")
    
    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["subject"] == "V2 Subject"
    assert "semantic_vector" not in doc, "Old embedding should not have been applied"

    # 3. Valid update V2 should work
    success = await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=vector,
        embedding_model_id="model_id",
        version=2
    )
    
    await es_clean_client.post(f"/{index_name}/_refresh")
    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert "semantic_vector" in doc, "Valid V2 embedding should have been applied"

    # 4. Tombstone Test
    # If the doc is tombstoned, it goes to V3 with is_deleted=True
    await es_adapter.index_message(
        index_name=index_name,
        document={"id": doc_id, "tenant_id": str(tenant_id), "version": 3, "is_deleted": True},
        version=3
    )
    
    new_vector = [0.2] * 1536
    # Try a stale embedding (even of the deleted payload) and make sure it does not resurrect or mutate badly
    await es_adapter.update_embeddings(
        index_name=index_name,
        document_id=doc_id,
        semantic_vector=new_vector,
        embedding_model_id="model_id",
        version=2
    )
    await es_clean_client.post(f"/{index_name}/_refresh")
    res = await es_clean_client.get(f"/{index_name}/_source/{doc_id}")
    doc = res.json()
    assert doc["is_deleted"] is True
    # If it was updated correctly, the version in Elasticsearch _source is 3 and _version wrapper is internal semantics.
    assert doc.get("semantic_vector") == vector, "V2 embedding retry should be blocked and not overwrite tombstoned or updated ES source"
    
    # Clean up index
    await es_clean_client.delete(f"/{index_name}", follow_redirects=True)
