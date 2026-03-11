"""Inference backends for evals — Ollama and LM Studio (OpenAI-compatible)."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from shared.config import AlfredConfig

_config = AlfredConfig.from_env()

# Shared long-lived client for all backends
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _http_client


class InferFn(Protocol):
    """Protocol for inference callables."""

    async def __call__(self, prompt: str, model: str) -> dict[str, Any]: ...


async def infer_ollama(prompt: str, model: str) -> dict[str, Any]:
    """Infer via Ollama /api/chat."""
    client = _get_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    resp = await client.post(
        f"{_config.ollama_host}/api/chat",
        json={"model": model, "messages": messages, "stream": False, "format": "json"},
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "response": data.get("message", {}).get("content", ""),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
    }


async def infer_lmstudio(prompt: str, model: str) -> dict[str, Any]:
    """Infer via LM Studio OpenAI-compatible /v1/chat/completions."""
    client = _get_client()
    host = _config.lmstudio_host
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    resp = await client.post(
        f"{host}/v1/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        },
    )
    resp.raise_for_status()
    data = resp.json()

    choice = data.get("choices", [{}])[0]
    usage = data.get("usage", {})
    return {
        "response": choice.get("message", {}).get("content", ""),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


BACKENDS: dict[str, InferFn] = {
    "ollama": infer_ollama,
    "lmstudio": infer_lmstudio,
}
