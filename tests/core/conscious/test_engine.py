"""Tests for ConsciousEngine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.engine import MAX_ITERATIONS, ConsciousEngine
from core.identity.schemas import IdentityResult
from core.reflex.tool_registry import ToolInfo


def _make_request(**overrides: object) -> UserRequest:
    defaults = {
        "source": "web-pwa",
        "channel": "web_pwa",
        "session_id": "sess-1",
        "identity_claim": "sir",
        "authenticated": True,
        "content_type": "text",
        "content": "Hello",
    }
    defaults.update(overrides)
    return UserRequest(**defaults)


def _sir_identity() -> IdentityResult:
    return IdentityResult(
        identity="sir",
        confidence=0.99,
        method="webauthn",
        factors=["webauthn"],
        risk_clearance="high",
    )


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock]:
    deps: dict[str, AsyncMock | MagicMock] = {
        "redis": AsyncMock(),
        "identity_gate": MagicMock(),
        "session_mgr": AsyncMock(),
        "cost_tracker": AsyncMock(),
        "context_assembler": MagicMock(),
        "domain_router": AsyncMock(),
        "tool_registry": AsyncMock(),
        "context_reader": AsyncMock(),
    }
    # Common defaults
    deps["identity_gate"].resolve.return_value = _sir_identity()
    deps["session_mgr"].get_or_create.return_value = {"channel": "web_pwa", "history": []}
    deps["cost_tracker"].is_budget_exceeded.return_value = False
    deps["context_assembler"].assemble.return_value = "You are Alfred."
    deps["tool_registry"].get_tools.return_value = []
    deps["context_reader"].get_rendered_context.return_value = ""
    return deps


@pytest.mark.asyncio
async def test_process_request_basic(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    engine = ConsciousEngine(**mock_deps)

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Good evening, sir.", [], 100, 50)
        response = await engine.process_request(_make_request())

    assert isinstance(response, AlfredResponse)
    assert response.text == "Good evening, sir."


@pytest.mark.asyncio
async def test_budget_exceeded_returns_fallback(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    mock_deps["cost_tracker"].is_budget_exceeded.return_value = True
    engine = ConsciousEngine(**mock_deps)

    response = await engine.process_request(_make_request(content="Good morning"))
    assert isinstance(response, AlfredResponse)
    assert "budget" in response.text.lower() or "reduced" in response.text.lower()


@pytest.mark.asyncio
async def test_multi_turn_tool_use(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Agentic loop: LLM calls a tool, gets result, then responds."""
    tool = ToolInfo(
        name="smart_home.get_lights",
        description="Get light state",
        parameters={},
        feature_name="home",
        feature_description="Home control",
        target_service="home-service",
    )
    mock_deps["tool_registry"].get_tools.return_value = [tool]

    action_result = MagicMock()
    action_result.status = "success"
    action_result.result = {"lights": "on"}
    mock_deps["domain_router"].route.return_value = action_result

    engine = ConsciousEngine(**mock_deps)

    tool_call = [{"id": "tc-1", "name": "smart_home.get_lights", "input": {}}]

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        # First call: LLM uses a tool. Second call: LLM responds with text.
        mock_llm.side_effect = [
            ("Let me check...", tool_call, 100, 50),
            ("The lights are on, sir.", [], 150, 60),
        ]
        response = await engine.process_request(_make_request(content="Are the lights on?"))

    assert response.text == "The lights are on, sir."
    assert "smart_home.get_lights" in response.actions_taken
    assert mock_llm.call_count == 2


@pytest.mark.asyncio
async def test_tool_not_found_in_registry(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Tool call for a tool not in registry returns error result."""
    mock_deps["tool_registry"].get_tools.return_value = []  # empty registry
    engine = ConsciousEngine(**mock_deps)

    results = await engine._execute_tool_calls(
        [{"id": "tc-1", "name": "nonexistent.tool", "input": {}}], tools=[]
    )

    assert len(results) == 1
    assert results[0]["type"] == "tool_result"
    assert "not found" in results[0]["content"]


@pytest.mark.asyncio
async def test_max_iterations_fallback(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """When tool calls never resolve, engine hits MAX_ITERATIONS and returns fallback."""
    tool = ToolInfo(
        name="some.tool",
        description="A tool",
        parameters={},
        feature_name="test",
        feature_description="Test",
        target_service="test-service",
    )
    mock_deps["tool_registry"].get_tools.return_value = [tool]

    action_result = MagicMock()
    action_result.status = "success"
    action_result.result = {"ok": True}
    mock_deps["domain_router"].route.return_value = action_result

    engine = ConsciousEngine(**mock_deps)

    # LLM always returns a tool call, never a final text-only response
    tool_call = [{"id": "tc-1", "name": "some.tool", "input": {}}]
    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("", tool_call, 100, 50)
        response = await engine.process_request(_make_request())

    assert mock_llm.call_count == MAX_ITERATIONS
    assert "apologize" in response.text.lower() or "deliberating" in response.text.lower()


def test_tools_to_openai_format(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Verify ToolInfo → OpenAI function-calling format conversion."""
    engine = ConsciousEngine(**mock_deps)
    tools = [
        ToolInfo(
            name="smart_home.set_light",
            description="Set light brightness",
            parameters={
                "room": {"type": "str", "description": "Room name"},
                "level": {"type": "int", "description": "Brightness", "default": 100},
            },
            feature_name="home",
            feature_description="Home",
            target_service="home-service",
        ),
    ]

    result = engine._tools_to_openai_format(tools)

    assert len(result) == 1
    assert result[0]["type"] == "function"
    func = result[0]["function"]
    # Dots sanitized to underscores for OpenAI compatibility
    assert func["name"] == "smart_home_set_light"
    schema = func["parameters"]
    assert "room" in schema["properties"]
    assert "level" in schema["properties"]
    # Python types mapped to JSON Schema types
    assert schema["properties"]["room"]["type"] == "string"
    assert schema["properties"]["level"]["type"] == "integer"
    # room has no default → required; level has default → not required
    assert "room" in schema["required"]
    assert "level" not in schema["required"]


def test_tool_name_sanitization(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Tool names with dots are sanitized for OpenAI and reversed back."""
    engine = ConsciousEngine(**mock_deps)
    assert engine._sanitize_tool_name("lighting.dim_lights") == "lighting_dim_lights"

    tools = [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim",
            parameters={},
            feature_name="home",
            feature_description="Home",
            target_service="home-service",
        ),
    ]
    assert engine._unsanitize_tool_name("lighting_dim_lights", tools) == "lighting.dim_lights"
