"""Mocked Ollama client for deterministic regression testing.

Provides canned SLM responses keyed by entity ID substring matching.
No network calls, no GPU, fast CI runs.
"""

from __future__ import annotations

from typing import Any


class MockOllamaClient:
    """Drop-in replacement for ollama_client.infer() in regression mode."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}

    def infer_sync(self, prompt: str) -> dict[str, Any]:
        """Synchronous inference — match prompt against canned responses."""
        for key, response in self._responses.items():
            if key in prompt:
                return {"response": response}
        return {"response": '{"action": "none"}'}

    async def infer(self, prompt: str) -> dict[str, Any]:
        """Async interface matching ollama_client.infer()."""
        return self.infer_sync(prompt)
