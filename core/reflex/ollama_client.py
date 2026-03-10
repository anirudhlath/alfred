"""Thin async client for Ollama's generate API."""

from __future__ import annotations

import os

import httpx

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "llama3:8b"


async def infer(prompt: str, model: str | None = None) -> dict[str, object]:
    """Send a prompt to Ollama and return the response with token counts."""
    model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "response": data.get("response", ""),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
    }
