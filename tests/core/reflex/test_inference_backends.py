"""Runtime reflex inference backend selection (REFLEX_BACKEND=ollama|openai)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest


class _StubAsyncClient:
    """Captures the request and returns a canned httpx.Response."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.is_closed = False

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        self.requests.append((url, json))
        return httpx.Response(
            self.status_code, json=self.payload, request=httpx.Request("POST", url)
        )

    async def get(self, url: str) -> httpx.Response:
        self.requests.append((url, {}))
        return httpx.Response(
            self.status_code, json=self.payload, request=httpx.Request("GET", url)
        )


async def test_dispatcher_defaults_to_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reflex import inference, ollama_client, openai_client

    monkeypatch.delenv("REFLEX_BACKEND", raising=False)
    calls: list[str] = []

    async def _fake_ollama(prompt: str, model: str | None = None) -> dict[str, object]:
        calls.append("ollama")
        return {"response": "{}"}

    async def _fake_openai(prompt: str, model: str | None = None) -> dict[str, object]:
        calls.append("openai")
        return {"response": "{}"}

    monkeypatch.setattr(ollama_client, "infer", _fake_ollama)
    monkeypatch.setattr(openai_client, "infer", _fake_openai)
    await inference.infer("hello")
    assert calls == ["ollama"]


async def test_dispatcher_selects_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reflex import inference, ollama_client, openai_client

    monkeypatch.setenv("REFLEX_BACKEND", "openai")
    calls: list[str] = []

    async def _fake_ollama(prompt: str, model: str | None = None) -> dict[str, object]:
        calls.append("ollama")
        return {"response": "{}"}

    async def _fake_openai(prompt: str, model: str | None = None) -> dict[str, object]:
        calls.append("openai")
        return {"response": "{}"}

    monkeypatch.setattr(ollama_client, "infer", _fake_ollama)
    monkeypatch.setattr(openai_client, "infer", _fake_openai)
    await inference.infer("hello")
    assert calls == ["openai"]


async def test_dispatcher_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reflex import inference

    monkeypatch.setenv("REFLEX_BACKEND", "banana")
    with pytest.raises(RuntimeError, match="REFLEX_BACKEND"):
        await inference.infer("hello")


async def test_openai_infer_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reflex import openai_client

    monkeypatch.setenv("OPENAI_COMPAT_HOST", "http://vllm:8000")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "gemma-27b")
    stub = _StubAsyncClient(
        {
            "choices": [{"message": {"content": '{"action": "none"}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    )
    monkeypatch.setattr(openai_client, "_get_client", lambda: stub)

    result = await openai_client.infer("decide")

    url, body = stub.requests[0]
    assert url == "http://vllm:8000/v1/chat/completions"
    assert body["model"] == "gemma-27b"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "user", "content": "decide"}]
    assert result["response"] == '{"action": "none"}'
    assert result["prompt_tokens"] == 11
    assert result["completion_tokens"] == 7
    assert result["total_tokens"] == 18


async def test_openai_infer_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reflex import openai_client

    monkeypatch.setenv("REFLEX_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_COMPAT_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_MODEL"):
        await openai_client.infer("decide")


async def test_openai_warmup_pings_models_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reflex import openai_client

    monkeypatch.setenv("OPENAI_COMPAT_HOST", "http://vllm:8000")
    stub = _StubAsyncClient({"data": [{"id": "gemma-27b"}]})
    monkeypatch.setattr(openai_client, "_get_client", lambda: stub)

    await openai_client.warmup()

    url, _ = stub.requests[0]
    assert url == "http://vllm:8000/v1/models"
