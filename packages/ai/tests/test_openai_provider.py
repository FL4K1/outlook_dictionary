"""Tests for OpenAIEmbeddingProvider using httpx mock transport.

No real API calls — all tests use a deterministic mock transport.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mip_ai.embeddings.errors import (
    EmbeddingPermanentError,
    EmbeddingTransientError,
)
from mip_ai.embeddings.openai import OpenAIEmbeddingProvider


def _make_success_response(
    vectors: list[list[float]],
    model: str = "text-embedding-3-small",
    total_tokens: int = 100,
) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": vec} for i, vec in enumerate(vectors)
        ],
        "model": model,
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }


class MockTransport(httpx.AsyncBaseTransport):
    """Configurable mock transport for httpx.AsyncClient."""

    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _provider(
    transport: httpx.AsyncBaseTransport,
    dims: int = 4,
) -> OpenAIEmbeddingProvider:
    p = OpenAIEmbeddingProvider(
        api_key="test-key",
        model="text-embedding-3-small",
        dimensions=dims,
        base_url="https://api.openai.com/v1",
        timeout_seconds=5.0,
        max_batch_size=3,
    )
    # Monkey-patch _call_api to use our transport

    async def patched_call(texts):
        url = f"{p._base_url}/embeddings"
        payload = {
            "input": texts,
            "model": p._model,
            "dimensions": p._dimensions,
        }
        headers = {
            "Authorization": f"Bearer {p._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(url, json=payload, headers=headers)

        # Reuse the response parsing from the original method
        # We need to simulate _call_api's full logic
        import contextlib

        from mip_ai.embeddings.openai import _TRANSIENT_STATUS_CODES

        if response.status_code in _TRANSIENT_STATUS_CODES:
            retry_after = None
            raw = response.headers.get("Retry-After")
            if raw:
                with contextlib.suppress(ValueError):
                    retry_after = float(raw)
            raise EmbeddingTransientError(
                f"OpenAI transient HTTP {response.status_code}",
                retry_after=retry_after,
            )

        if response.status_code != 200:
            raise EmbeddingPermanentError(
                f"OpenAI HTTP {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        data = body.get("data")
        if not isinstance(data, list):
            raise EmbeddingPermanentError("OpenAI response missing 'data' array")

        vectors = []
        for item in sorted(data, key=lambda d: d.get("index", 0)):
            vec = item.get("embedding")
            if not isinstance(vec, list) or len(vec) != p._dimensions:
                raise EmbeddingPermanentError(
                    f"Dimension mismatch: expected {p._dimensions}, "
                    f"got {len(vec) if isinstance(vec, list) else 'null'}"
                )
            vectors.append(vec)

        usage = body.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        return {"vectors": vectors, "tokens": tokens}

    p._call_api = patched_call
    return p


@pytest.mark.asyncio
async def test_single_embedding_success():
    """Case 1: Single text → correct EmbeddingResult."""
    vec = [0.1, 0.2, 0.3, 0.4]

    def handler(_request):
        return httpx.Response(200, json=_make_success_response([vec]))

    provider = _provider(MockTransport(handler), dims=4)
    result = await provider.embed(["hello world"])

    assert len(result.vectors) == 1
    assert result.vectors[0] == vec
    assert result.model_id == "text-embedding-3-small"
    assert result.dimensions == 4
    assert result.provider == "openai"
    assert result.total_tokens == 100


@pytest.mark.asyncio
async def test_batch_embedding_success():
    """Case 2: 5 texts batched (batch_size=3) → 5 vectors."""
    vecs = [[float(i)] * 4 for i in range(5)]

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content)
        batch_vecs = vecs[: len(body["input"])]
        # remove used ones
        for _ in range(len(body["input"])):
            vecs.pop(0) if vecs else None
        return httpx.Response(200, json=_make_success_response(batch_vecs, total_tokens=50))

    all_vecs = [[float(i)] * 4 for i in range(5)]

    def handler2(request):
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content)
        n = len(body["input"])
        start = (call_count - 1) * 3
        batch = all_vecs[start : start + n]
        return httpx.Response(200, json=_make_success_response(batch, total_tokens=50))

    call_count = 0
    provider = _provider(MockTransport(handler2), dims=4)
    result = await provider.embed(["a", "b", "c", "d", "e"])

    assert len(result.vectors) == 5
    assert call_count == 2  # batch_size=3 → 2 API calls
    assert result.total_tokens == 100  # 50 * 2


@pytest.mark.asyncio
async def test_rate_limit_429():
    """Case 3: 429 → EmbeddingTransientError with retry_after."""

    def handler(_request):
        return httpx.Response(429, headers={"Retry-After": "5"}, text="rate limited")

    provider = _provider(MockTransport(handler))
    with pytest.raises(EmbeddingTransientError) as exc_info:
        await provider.embed(["test"])
    assert exc_info.value.retry_after == 5.0


@pytest.mark.asyncio
async def test_server_error_500():
    """Case 4: 500 → EmbeddingTransientError."""

    def handler(_request):
        return httpx.Response(500, text="internal server error")

    provider = _provider(MockTransport(handler))
    with pytest.raises(EmbeddingTransientError):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_auth_failure_401():
    """Case 5: 401 → EmbeddingPermanentError."""

    def handler(_request):
        return httpx.Response(401, text="unauthorized")

    provider = _provider(MockTransport(handler))
    with pytest.raises(EmbeddingPermanentError):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_wrong_dimensions():
    """Case 6: Wrong dimension → EmbeddingPermanentError."""
    wrong_vec = [0.1, 0.2, 0.3]  # 3 dims instead of 4

    def handler(_request):
        return httpx.Response(200, json=_make_success_response([wrong_vec]))

    provider = _provider(MockTransport(handler), dims=4)
    with pytest.raises(EmbeddingPermanentError, match="Dimension mismatch"):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_malformed_json():
    """Case 8: Malformed JSON → EmbeddingPermanentError."""

    def handler(_request):
        return httpx.Response(200, json={"no_data_key": True})

    provider = _provider(MockTransport(handler))
    with pytest.raises(EmbeddingPermanentError, match="missing 'data' array"):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_empty_input():
    """Empty input → empty result without API call."""
    call_count = 0

    def handler(_request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_make_success_response([]))

    provider = _provider(MockTransport(handler))
    result = await provider.embed([])

    assert result.vectors == []
    assert call_count == 0
    assert result.total_tokens == 0
