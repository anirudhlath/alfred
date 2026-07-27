"""Thin async client for OpenAI-compatible chat APIs (vLLM, LM Studio, …).

Runtime sibling of :mod:`core.reflex.ollama_client` behind the
:mod:`core.reflex.inference` backend seam. Env is read per call (not at import)
so ``REFLEX_BACKEND``/``OPENAI_COMPAT_*`` changes are picked up without a
process restart in tests.
"""

from __future__ import annotations

from typing import Any

import httpx

from sdk.alfred_sdk.telemetry import track_tokens
from shared.config import AlfredConfig

# Long-lived client reuses TCP connections across inference calls (hot path)
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


def _require_model(config: AlfredConfig) -> str:
    if not config.openai_compat_model:
        raise RuntimeError(
            "REFLEX_BACKEND=openai requires OPENAI_COMPAT_MODEL "
            "(the model name the server exposes, e.g. vLLM's --served-model-name)"
        )
    return config.openai_compat_model


async def warmup(model: str | None = None) -> None:
    """Confirm the server is up and models are loaded (GET /v1/models).

    OpenAI-compatible servers (vLLM) load weights at server start, so unlike
    Ollama there is nothing to pre-load — this is a liveness gate only. No
    telemetry decorator so warmup pings never pollute research token data.
    """
    config = AlfredConfig.from_env()
    client = _get_client()
    resp = await client.get(f"{config.openai_compat_host}/v1/models")
    resp.raise_for_status()


@track_tokens(model="openai-compat")
async def infer(prompt: str, model: str | None = None) -> dict[str, object]:
    """Send a prompt via /v1/chat/completions and return the response."""
    config = AlfredConfig.from_env()
    model = model or _require_model(config)
    client = _get_client()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    resp = await client.post(
        f"{config.openai_compat_host}/v1/chat/completions",
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
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return {
        "response": choice.get("message", {}).get("content", ""),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": usage.get("total_tokens", prompt_tokens + completion_tokens),
    }
