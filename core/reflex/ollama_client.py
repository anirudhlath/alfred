"""Thin async client for Ollama's chat API."""

from __future__ import annotations

from typing import Any

import httpx

from sdk.alfred_sdk.telemetry import track_tokens
from shared.config import AlfredConfig

_config = AlfredConfig.from_env()

# Long-lived client reuses TCP connections across inference calls (hot path)
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def warmup(model: str | None = None) -> None:
    """Load the model into Ollama's memory without generating.

    An empty ``messages`` list is Ollama's documented model-load request; no
    telemetry decorator so warmup pings never pollute research token data.
    """
    model = model or _config.ollama_model
    client = _get_client()
    resp = await client.post(
        f"{_config.ollama_host}/api/chat",
        json={"model": model, "messages": [], "stream": False},
    )
    resp.raise_for_status()


@track_tokens(model="ollama")
async def infer(prompt: str, model: str | None = None) -> dict[str, object]:
    """Send a prompt to Ollama via the chat API and return the response."""
    model = model or _config.ollama_model
    client = _get_client()

    # Split prompt at the last "## Your Decision" to separate system/user content
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    resp = await client.post(
        f"{_config.ollama_host}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "format": "json",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "response": data.get("message", {}).get("content", ""),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
    }
