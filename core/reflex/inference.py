"""Reflex inference backend dispatcher (env ``REFLEX_BACKEND``: ollama | openai).

``ollama`` (default) talks to Ollama's native ``/api/chat``; ``openai`` talks to
any OpenAI-compatible ``/v1/chat/completions`` server (vLLM, LM Studio). The
backend is resolved per call so it stays testable and env-driven — this is the
runtime counterpart of ``evals/inference.py``'s BACKENDS table.
"""

from __future__ import annotations

import os

from core.reflex import ollama_client, openai_client

_BACKENDS = ("ollama", "openai")


def _backend() -> str:
    name = os.getenv("REFLEX_BACKEND", "ollama").strip().lower() or "ollama"
    if name not in _BACKENDS:
        raise RuntimeError(
            f"Unknown REFLEX_BACKEND {name!r} (expected one of: {', '.join(_BACKENDS)})"
        )
    return name


async def warmup(model: str | None = None) -> None:
    """Warm the selected backend (model pre-load for Ollama, liveness for OpenAI)."""
    if _backend() == "openai":
        await openai_client.warmup(model)
    else:
        await ollama_client.warmup(model)


async def infer(prompt: str, model: str | None = None) -> dict[str, object]:
    """Run one reflex decision through the selected backend."""
    # @track_tokens erases the clients' return type to Any — pin it back here.
    result: dict[str, object]
    if _backend() == "openai":
        result = await openai_client.infer(prompt, model)
    else:
        result = await ollama_client.infer(prompt, model)
    return result
