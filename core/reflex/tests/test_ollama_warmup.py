"""Tests for the Ollama model warmup call."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.reflex import ollama_client


@pytest.mark.asyncio
async def test_warmup_posts_empty_chat_to_load_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warmup sends an empty-messages chat request — Ollama's documented way to
    load a model into memory without generating anything."""
    fake_client = MagicMock()
    fake_client.is_closed = False
    response = MagicMock()
    response.raise_for_status = MagicMock()
    fake_client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(ollama_client, "_http_client", fake_client)

    await ollama_client.warmup(model="test-model")

    fake_client.post.assert_awaited_once()
    args, kwargs = fake_client.post.call_args
    assert args[0].endswith("/api/chat")
    assert kwargs["json"]["model"] == "test-model"
    assert kwargs["json"]["messages"] == []
    response.raise_for_status.assert_called_once()
