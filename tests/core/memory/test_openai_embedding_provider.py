from __future__ import annotations

import httpx
import pytest

from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider


def _provider(
    handler: object,
    *,
    dim: int = 4,
    model: str = "BAAI/bge-m3",
) -> OpenAICompatEmbeddingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenAICompatEmbeddingProvider(
        model_name=model, host="http://embed:8001", dim=dim, client=client
    )


@pytest.mark.asyncio
async def test_embed_returns_the_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]})

    provider = _provider(handler)
    assert await provider.embed("hello") == [0.1, 0.2, 0.3, 0.4]


@pytest.mark.asyncio
async def test_embed_sends_model_and_input() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    await provider.embed("hello")
    assert seen["model"] == "BAAI/bge-m3"
    assert seen["input"] == ["hello"]


@pytest.mark.asyncio
async def test_embed_batch_reorders_by_index() -> None:
    """The API may return items out of order — index, not position, is authoritative."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "embedding": [2.0] * 4},
                    {"index": 0, "embedding": [0.0] * 4},
                    {"index": 1, "embedding": [1.0] * 4},
                ]
            },
        )

    provider = _provider(handler)
    results = await provider.embed_batch(["a", "b", "c"])
    assert [r[0] for r in results] == [0.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_embed_batch_empty_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("empty input must not hit the server")

    provider = _provider(handler)
    assert await provider.embed_batch([]) == []


@pytest.mark.asyncio
async def test_embed_raises_on_short_response() -> None:
    """A truncated batch response must fail loudly, not return fewer vectors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="returned 1 embedding"):
        await provider.embed_batch(["a", "b"])


@pytest.mark.asyncio
async def test_embed_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    provider = _provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed("hello")


def test_dimension_and_model_name_need_no_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("metadata accessors must not make requests")

    provider = _provider(handler, dim=1024)
    assert provider.dimension() == 1024
    assert provider.model_name() == "BAAI/bge-m3"


@pytest.mark.asyncio
async def test_warmup_rejects_a_dimension_mismatch() -> None:
    """Configured dim must match what the server actually returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 8}]})

    provider = _provider(handler, dim=4)
    with pytest.raises(RuntimeError, match="EMBEDDING_DIM"):
        await provider.warmup()


@pytest.mark.asyncio
async def test_warmup_passes_when_dimensions_agree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler, dim=4)
    await provider.warmup()
