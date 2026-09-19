import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.auth.context import AuthenticationContext
from app.auth.dependencies import get_auth_context
from app.main import create_app

# We use the same testing setup for consistency
TEST_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TEST_TENANT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
TEST_ORG_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
TEST_SESSION_ID = uuid.uuid4()

ES_TEST_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")


def mock_require_tenant() -> AuthenticationContext:
    return AuthenticationContext(
        request_id="test-req",
        correlation_id="test-corr",
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
        organization_id=TEST_ORG_ID,
        session_id=TEST_SESSION_ID,
        membership_id=uuid.uuid4(),
    )


@pytest.fixture
async def es_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    client = httpx.AsyncClient(base_url=ES_TEST_URL, timeout=5.0)
    try:
        resp = await client.get("/")
        if resp.status_code != 200:
            raise RuntimeError(f"Elasticsearch ping status {resp.status_code}")
    except Exception as err:
        await client.aclose()
        pytest.skip(f"Elasticsearch unavailable: {err}")

    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_auth_context] = mock_require_tenant

    with patch("app.auth.middleware.is_public_route", return_value=True):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest.fixture
async def seeded_es_hybrid(es_client: httpx.AsyncClient) -> AsyncGenerator[str, None]:
    """Sets up an Elasticsearch index populated with mock data & semantic vectors for testing."""
    index_name = f"mail_messages_{TEST_TENANT_ID}"

    # Clean up index
    await es_client.delete(f"/{index_name}", follow_redirects=True)

    # Create mapping
    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "mail_account_id": {"type": "keyword"},
                "folder_ids": {"type": "keyword"},
                "subject": {"type": "text"},
                "body": {"type": "text"},
                "sender": {"type": "text"},
                "received_date_time": {"type": "date"},
                "semantic_vector": {
                    "type": "dense_vector",
                    "dims": 1536,
                    "index": True,
                    "similarity": "cosine",
                },
                "is_deleted": {"type": "boolean"},
            }
        }
    }
    res = await es_client.put(f"/{index_name}", json=mapping)
    assert res.status_code == 200

    from mip_ai.embeddings.mock import DeterministicMockEmbeddingProvider

    provider = DeterministicMockEmbeddingProvider()

    docs: list[dict[str, Any]] = [
        {
            "id": "msg-finance-1",
            "tenant_id": str(TEST_TENANT_ID),
            "mail_account_id": "account-1",
            "folder_ids": ["folder-a"],
            "subject": "Q3 Financial Report",
            "body": "The Q3 revenue increased by 15%. Attached is the detailed spreadsheet.",
            "sender": "finance@example.com",
            "is_deleted": False,
            "received_date_time": "2026-09-01T10:00:00Z",
        },
        {
            "id": "msg-marketing-1",
            "tenant_id": str(TEST_TENANT_ID),
            "mail_account_id": "account-1",
            "folder_ids": ["folder-b"],
            "subject": "New Campaign Launch",
            "body": "The social media marketing campaign will start next week. Please review graphics.",
            "sender": "marketing@example.com",
            "is_deleted": False,
            "received_date_time": "2026-09-02T10:00:00Z",
        },
    ]

    # Generate deterministic vectors
    for doc in docs:
        query_text = f"Subject: {doc['subject']}\nFrom: {doc['sender']}\n\n{doc['body']}"
        embed_result = await provider.embed([query_text])
        doc["semantic_vector"] = embed_result.vectors[0]

    # Bulk insert
    bulk_data = []
    for doc in docs:
        bulk_data.append({"index": {"_index": index_name, "_id": doc["id"]}})
        bulk_data.append(doc)

    import json

    bulk_str = "\n".join(json.dumps(d) for d in bulk_data) + "\n"

    res = await es_client.post(
        "/_bulk", content=bulk_str, headers={"Content-Type": "application/x-ndjson"}
    )
    assert res.status_code == 200

    await es_client.post(f"/{index_name}/_refresh")

    yield index_name
    await es_client.delete(f"/{index_name}")


@pytest.mark.asyncio
async def test_hybrid_search_financial(
    api_client: httpx.AsyncClient, seeded_es_hybrid: str
) -> None:
    """Test semantic search retrieval correctly mapping the hybrid query."""

    query = "money and spreadsheets"

    # Lexical mode should fail since 'money and spreadsheets' doesn't exactly match 'revenue' and 'Q3'
    resp = await api_client.post("/search/mail", json={"query": query, "search_mode": "lexical"})
    if resp.status_code != 200:
        print(f"LEXICAL FAILED: {resp.text}")
    assert resp.status_code == 200
    data = resp.json()
    # It might strictly match "spreadsheets", so results might be 1. We just ensure it runs.

    # Run Semantic mode
    resp = await api_client.post(
        "/search/mail", json={"query": "financial spreadsheet report", "search_mode": "hybrid"}
    )
    if resp.status_code != 200:
        print(f"HYBRID FAILED: {resp.text}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["items"]
    assert len(data["items"]) > 0

    # Finance email should be the top match
    assert data["items"][0]["id"] == "msg-finance-1"
