"""Tests for System 1 regression mode."""

from __future__ import annotations

import pytest

from evals.regression.mock_ollama import MockOllamaClient
from evals.regression.runner import _check_scenario


def test_mock_ollama_returns_canned_response() -> None:
    client = MockOllamaClient(
        responses={
            "light.living_room": (
                '{"tool_name": "smart_home.dim_lights", "target_service": "home-service",'
                ' "parameters": {"room": "living_room", "level": 50}}'
            ),
        }
    )
    response = client.infer_sync("light.living_room turned on")
    assert "tool_name" in response["response"]


def test_mock_ollama_default_no_action() -> None:
    client = MockOllamaClient(responses={})
    response = client.infer_sync("some unknown event")
    assert '"action": "none"' in response["response"]


@pytest.mark.asyncio
async def test_mock_ollama_async_infer() -> None:
    client = MockOllamaClient(responses={"light.kitchen": '{"action": "dim"}'})
    response = await client.infer("light.kitchen on")
    assert "action" in response["response"]


def test_check_scenario_negative_none() -> None:
    """Negative scenario (expected: null) passes when response is no-action."""
    scenario = {"expected": None}
    response = {"response": '{"action": "none"}'}
    assert _check_scenario(scenario, response) is True


def test_check_scenario_negative_fails_on_action() -> None:
    """Negative scenario fails when response contains an action."""
    scenario = {"expected": None}
    response = {"response": '{"tool_name": "lights.dim"}'}
    assert _check_scenario(scenario, response) is False


def test_check_scenario_positive_match() -> None:
    """Positive scenario passes when tool_name and params match."""
    scenario = {
        "expected": {
            "tool_name": "lighting.dim_lights",
            "parameters": {"room": "living_room"},
        }
    }
    response = {
        "response": (
            '{"tool_name": "lighting.dim_lights",'
            ' "parameters": {"room": "living_room", "level": 50}}'
        ),
    }
    assert _check_scenario(scenario, response) is True


def test_check_scenario_positive_wrong_tool() -> None:
    """Positive scenario fails when tool_name doesn't match."""
    scenario = {
        "expected": {
            "tool_name": "lighting.dim_lights",
        }
    }
    response = {"response": '{"tool_name": "lighting.turn_off"}'}
    assert _check_scenario(scenario, response) is False


def test_check_scenario_positive_wrong_param() -> None:
    """Positive scenario fails when param values don't match."""
    scenario = {
        "expected": {
            "tool_name": "lighting.dim_lights",
            "parameters": {"room": "bedroom"},
        }
    }
    response = {
        "response": '{"tool_name": "lighting.dim_lights", "parameters": {"room": "living_room"}}'
    }
    assert _check_scenario(scenario, response) is False
