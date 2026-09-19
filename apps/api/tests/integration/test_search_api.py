"""Integration tests for Mail Search API (PR-2.7)."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from app.auth.context import AuthenticationContext
from app.auth.dependencies import get_auth_context
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

TEST_TENANT_ID = uuid.uuid4()
TEST_USER_ID = uuid.uuid4()
TEST_SESSION_ID = uuid.uuid4()
TEST_ORG_ID = uuid.uuid4()

ES_TEST_URL = os.environ.get("TEST_ELASTICSEARCH_URL", "http://localhost:9200")


def mock_require_tenant() -> AuthenticationContext:
    """Mock the authentication dependency returning a specific tenant."""
    return AuthenticationContext(
        request_id="test-req",
        correlation_id="test-corr",
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
        organization_id=TEST_ORG_ID,
        session_id=TEST_SESSION_ID,
        membership_id=uuid.uuid4(),
    )


def mock_require_tenant_unauthorized() -> AuthenticationContext:
    from app.auth.exceptions import AuthenticationError

    raise AuthenticationError("No active tenant membership.")


@pytest.fixture
async def es_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    client = httpx.AsyncClient(base_url=ES_TEST_URL, timeout=5.0)
    try:
        resp = await client.get("/")
        if resp.status_code != 200:
            raise RuntimeError(f"Elasticsearch ping status {resp.status_code}")
    except Exception as err:
        await client.aclose()
        if os.getenv("CI") == "true":
            pytest.fail(f"Elasticsearch required by CI is unavailable: {err}")
        pytest.skip(f"Elasticsearch unavailable: {err}")

    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Test client for FastAPI."""
    app = create_app()
    app.dependency_overrides[get_auth_context] = mock_require_tenant

    with patch("app.auth.middleware.is_public_route", return_value=True):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest.fixture
async def seeded_es(es_client: httpx.AsyncClient) -> AsyncGenerator[str, None]:
    """Sets up an Elasticsearch index populated with mock data for testing."""
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
                "subject": {"type": "text"},
                "body": {"type": "text"},
                "sender": {"type": "text"},
                "folder_ids": {"type": "keyword"},
                "is_read": {"type": "boolean"},
                "has_attachments": {"type": "boolean"},
                "is_deleted": {"type": "boolean"},
                "received_date_time": {"type": "date"},
                "semantic_vector": {
                    "type": "dense_vector",
                    "dims": 1536,
                    "index": True,
                    "similarity": "cosine",
                },
                "embedding_model_id": {"type": "keyword"},
                "embedding_version": {"type": "long"},
            }
        }
    }
    resp = await es_client.put(f"/{index_name}", json=mapping)
    assert resp.status_code in (200, 201), resp.text

    # Seed mock messages
    docs = [
        {
            "id": "msg-1",
            "tenant_id": str(TEST_TENANT_ID),
            "mail_account_id": "acc-1",
            "subject": "Hello World",
            "body": "Welcome to the test",
            "sender": "Alice",
            "folder_ids": ["folder-A"],
            "is_read": False,
            "has_attachments": True,
            "is_deleted": False,
            "received_date_time": "2026-01-01T10:00:00Z",
        },
        {
            "id": "msg-2",
            "tenant_id": str(TEST_TENANT_ID),
            "mail_account_id": "acc-1",
            "subject": "Important Meeting",
            "body": "Strategy review meeting notes",
            "sender": "Bob",
            "folder_ids": ["folder-B"],
            "is_read": True,
            "has_attachments": False,
            "is_deleted": False,
            "received_date_time": "2026-01-02T10:00:00Z",
        },
        {
            "id": "msg-3",
            "tenant_id": str(TEST_TENANT_ID),
            "mail_account_id": "acc-2",
            "subject": "Deleted Message",
            "body": "This is a tombstone",
            "sender": "Charlie",
            "folder_ids": ["folder-A"],
            "is_read": False,
            "has_attachments": False,
            "is_deleted": True,
            "received_date_time": "2026-01-03T10:00:00Z",
        },
    ]

    # Bulk insert (simpler for test to just PUT each doc)
    for doc in docs:
        r = await es_client.put(f"/{index_name}/_doc/{doc['id']}?refresh=true", json=doc)
        assert r.status_code in (200, 201), r.text

    # Insert a cross-tenant document in a different index
    other_tenant = uuid.uuid4()
    other_index = f"mail_messages_{other_tenant}"
    await es_client.delete(f"/{other_index}", follow_redirects=True)
    await es_client.put(f"/{other_index}", json=mapping)
    r = await es_client.put(
        f"/{other_index}/_doc/msg-4?refresh=true",
        json={
            "id": "msg-4",
            "tenant_id": str(other_tenant),
            "mail_account_id": "acc-1",
            "subject": "Secret Top Agent",
            "body": "Secret Message",
            "sender": "Dave",
            "folder_ids": ["folder-C"],
            "is_read": False,
            "has_attachments": False,
            "is_deleted": False,
            "received_date_time": "2026-01-04T10:00:00Z",
        },
    )
    assert r.status_code in (200, 201), r.text

    try:
        yield index_name
    finally:
        await es_client.delete(f"/{index_name}", follow_redirects=True)
        await es_client.delete(f"/{other_index}", follow_redirects=True)


@pytest.mark.asyncio
async def test_search_authenticated_tenant_isolation(
    api_client: httpx.AsyncClient, seeded_es: str
) -> None:
    """Ensure search defaults to just the tenant's exact messages excluding tombstones."""
    resp = await api_client.post("/search/mail", json={})
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert "items" in data
    items = data["items"]

    assert len(items) == 2
    # Should be sorted chronologically desc
    assert items[0]["id"] == "msg-2"
    assert items[1]["id"] == "msg-1"

    # Secret document (msg-4/Dave) must not be returned
    assert not any(item["subject"] == "Secret Top Agent" for item in items)
    # Tombstone must not be returned
    assert not any(item["id"] == "msg-3" for item in items)


@pytest.mark.asyncio
async def test_search_unauthenticated() -> None:
    """Ensure unauthenticated access fails."""
    app = create_app()
    app.dependency_overrides[get_auth_context] = mock_require_tenant_unauthorized

    with patch("app.auth.middleware.is_public_route", return_value=True):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/search/mail", json={})
            assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_filters(api_client: httpx.AsyncClient, seeded_es: str) -> None:
    """Test standard MailSearchRequest filters."""
    # Test query
    resp = await api_client.post("/search/mail", json={"query": "Strategy"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "msg-2"

    # Test folder_ids and is_read
    resp = await api_client.post(
        "/search/mail", json={"folder_ids": ["folder-A"], "is_read": False}
    )
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "msg-1"

    # Test account_ids and dates
    resp = await api_client.post(
        "/search/mail", json={"account_ids": ["acc-1"], "from_date": "2026-01-02T00:00:00Z"}
    )
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "msg-2"


@pytest.mark.asyncio
async def test_search_pagination(api_client: httpx.AsyncClient, seeded_es: str) -> None:
    """Test search_after logic."""
    # Fetch page 1
    resp1 = await api_client.post("/search/mail", json={"page_size": 1})
    data1 = resp1.json()
    assert len(data1["items"]) == 1
    assert data1["items"][0]["id"] == "msg-2"
    cursor = data1.get("next_page_cursor")
    assert cursor is not None

    # Fetch page 2
    resp2 = await api_client.post("/search/mail", json={"page_size": 1, "search_after": cursor})
    data2 = resp2.json()
    assert len(data2["items"]) == 1
    cursor2 = data2.get("next_page_cursor")
    assert cursor2 is not None

    # Fetch page 3
    resp3 = await api_client.post("/search/mail", json={"page_size": 1, "search_after": cursor2})
    data3 = resp3.json()
    assert len(data3["items"]) == 0
    assert data3.get("next_page_cursor") is None


@pytest.mark.asyncio
async def test_search_malformed_request(api_client: httpx.AsyncClient, seeded_es: str) -> None:
    """Test validation errors for requests."""
    # Bad dates
    resp = await api_client.post(
        "/search/mail",
        json={"from_date": "2026-06-01T00:00:00Z", "to_date": "2025-06-01T00:00:00Z"},
    )
    assert resp.status_code == 400

    # Bad limits
    resp = await api_client.post("/search/mail", json={"page_size": 1000})
    assert resp.status_code == 422
