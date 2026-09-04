from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Callable


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    dim: int = 4,
    model: str = "BAAI/bge-m3",
    host: str = "http://embed:8001",
    api_key: str = "",
) -> OpenAICompatEmbeddingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatEmbeddingProvider(
        model_name=model, host=host, dim=dim, api_key=api_key, client=client
    )


def _ok(*vectors: list[float]) -> Callable[[httpx.Request], httpx.Response]:
    """Handler returning ``vectors`` in request order."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]},
        )

    return handler


async def test_embed_returns_the_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]})

    provider = _provider(handler)
    assert await provider.embed("hello") == [0.1, 0.2, 0.3, 0.4]


async def test_embed_sends_model_input_and_float_encoding() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    await provider.embed("hello")
    assert seen["model"] == "BAAI/bge-m3"
    assert seen["input"] == ["hello"]
    # Explicit float encoding rules out a base64 "embedding" string.
    assert seen["encoding_format"] == "float"


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


async def test_embed_batch_rejects_duplicate_indices() -> None:
    """Two items at index 0 survive the length check and mispair under a stable sort."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.0] * 4},
                    {"index": 0, "embedding": [1.0] * 4},
                ]
            },
        )

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match=r"indices \[0, 0\]"):
        await provider.embed_batch(["a", "b"])


async def test_embed_batch_empty_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("empty input must not hit the server")

    provider = _provider(handler)
    assert await provider.embed_batch([]) == []


async def test_embed_raises_on_short_response() -> None:
    """A truncated batch response must fail loudly, not return fewer vectors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="returned 1 embedding"):
        await provider.embed_batch(["a", "b"])


async def test_embed_error_carries_the_server_message() -> None:
    """vLLM explains exactly what is wrong; raise_for_status alone throws that away."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "object": "error",
                "message": "This model's maximum context length is 8192 tokens",
                "type": "BadRequestError",
            },
        )

    provider = _provider(handler)
    with pytest.raises(RuntimeError) as excinfo:
        await provider.embed("hello")
    message = str(excinfo.value)
    assert "400" in message
    assert "maximum context length is 8192 tokens" in message
    assert "http://embed:8001" in message
    assert "BAAI/bge-m3" in message
    assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)


async def test_embed_error_truncates_a_huge_body() -> None:
    """An HTML error page from a proxy must not paste kilobytes into every log line."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="x" * 20_000)

    provider = _provider(handler)
    with pytest.raises(RuntimeError) as excinfo:
        await provider.embed("hello")
    assert len(str(excinfo.value)) < 1_000


async def test_embed_wraps_a_connect_failure() -> None:
    """httpx's ConnectError says "Connection refused" and never names the host."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    provider = _provider(handler)
    with pytest.raises(RuntimeError) as excinfo:
        await provider.embed("hello")
    assert "http://embed:8001" in str(excinfo.value)
    assert "EMBEDDING_HOST" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


async def test_embed_raises_on_a_non_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="http://embed:8001"):
        await provider.embed("hello")


async def test_embed_raises_on_a_missing_embedding_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0}]})

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="embedding"):
        await provider.embed("hello")


async def test_embed_raises_on_a_base64_embedding() -> None:
    """Defence in depth behind encoding_format=float — a string is not a vector."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": "iZmZP83M"}]})

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="embedding"):
        await provider.embed("hello")


async def test_embed_raises_on_a_missing_data_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list"})

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="data"):
        await provider.embed("hello")


def test_dimension_and_model_name_need_no_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("metadata accessors must not make requests")

    provider = _provider(handler, dim=1024)
    assert provider.dimension() == 1024
    assert provider.model_name() == "BAAI/bge-m3"


async def test_embed_rejects_a_dimension_mismatch_without_warmup() -> None:
    """warmup() is best-effort (core/warmup.py logs and continues), so the real
    embed path must refuse wrong-width vectors on its own."""
    provider = _provider(_ok([1.0] * 8), dim=4)
    with pytest.raises(RuntimeError, match="EMBEDDING_DIM"):
        await provider.embed("hello")


async def test_embed_keeps_working_after_the_dimension_check_passes() -> None:
    provider = _provider(_ok([1.0] * 4), dim=4)
    assert len(await provider.embed("one")) == 4
    assert len(await provider.embed("two")) == 4


async def test_warmup_rejects_a_dimension_mismatch() -> None:
    """Configured dim must match what the server actually returns."""
    provider = _provider(_ok([1.0] * 8), dim=4)
    with pytest.raises(RuntimeError, match="EMBEDDING_DIM"):
        await provider.warmup()


async def test_warmup_passes_when_dimensions_agree() -> None:
    provider = _provider(_ok([1.0] * 4), dim=4)
    await provider.warmup()


async def test_api_key_is_sent_as_a_bearer_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler, api_key="sk-secret")
    await provider.embed("hello")
    assert seen["auth"] == "Bearer sk-secret"


async def test_no_authorization_header_without_an_api_key() -> None:
    seen: dict[str, bool] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_auth"] = "authorization" in request.headers
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler)
    await provider.embed("hello")
    assert seen["has_auth"] is False


async def test_a_trailing_slash_on_the_host_does_not_double_the_path() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    provider = _provider(handler, host="http://embed:8001/")
    await provider.embed("hello")
    assert seen["url"] == "http://embed:8001/v1/embeddings"


async def test_connect_timeout_is_short_even_with_an_injected_client() -> None:
    """Involuntary recall runs inline in the reply path — an unreachable host
    must fail fast, not stall a user-facing response for the read budget."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.extensions.get("timeout", {}))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatEmbeddingProvider(
        model_name="BAAI/bge-m3", host="http://embed:8001", dim=4, client=client, timeout=30.0
    )
    await provider.embed("hello")
    assert seen["connect"] == 5.0
    assert seen["read"] == 30.0


async def test_aclose_closes_a_client_it_created() -> None:
    provider = OpenAICompatEmbeddingProvider(
        model_name="BAAI/bge-m3", host="http://embed:8001", dim=4
    )
    await provider.aclose()
    assert provider._client.is_closed


async def test_aclose_leaves_an_injected_client_open() -> None:
    """The owner of an injected client closes it; a shared client must survive."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(_ok([1.0] * 4)))
    provider = OpenAICompatEmbeddingProvider(
        model_name="BAAI/bge-m3", host="http://embed:8001", dim=4, client=client
    )
    await provider.aclose()
    assert not client.is_closed
    await client.aclose()
