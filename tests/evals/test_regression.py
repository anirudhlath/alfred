"""Tests for System 1 regression mode."""

from __future__ import annotations

import pytest

from evals.regression.mock_ollama import MockOllamaClient


def test_mock_ollama_returns_canned_response() -> None:
    client = MockOllamaClient(responses={
        "light.living_room": (
            '{"tool_name": "smart_home.dim_lights", "target_service": "home-service",'
            ' "parameters": {"room": "living_room", "level": 50}}'
        ),
    })
    response = client.infer_sync("light.living_room turned on")
    assert "tool_name" in response["response"]


def test_mock_ollama_default_no_action() -> None:
    client = MockOllamaClient(responses={})
    response = client.infer_sync("some unknown event")
    assert '"action": "none"' in response["response"]


@pytest.mark.asyncio
async def test_mock_ollama_async_infer() -> None:
    client = MockOllamaClient(responses={
        "light.kitchen": '{"action": "dim"}'
    })
    response = await client.infer("light.kitchen on")
    assert "action" in response["response"]
