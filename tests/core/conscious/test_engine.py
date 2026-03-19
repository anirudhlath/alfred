"""Tests for ConsciousEngine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.engine import ConsciousEngine
from core.identity.schemas import IdentityResult


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock]:
    return {
        "redis": AsyncMock(),
        "identity_gate": MagicMock(),
        "session_mgr": AsyncMock(),
        "cost_tracker": AsyncMock(),
        "context_assembler": MagicMock(),
        "domain_router": AsyncMock(),
        "tool_registry": AsyncMock(),
        "context_reader": AsyncMock(),
    }


@pytest.mark.asyncio
async def test_process_request_basic(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    # Setup mocks
    mock_deps["identity_gate"].resolve.return_value = IdentityResult(
        identity="sir",
        confidence=0.99,
        method="webauthn",
        factors=["webauthn"],
        risk_clearance="high",
    )
    mock_deps["session_mgr"].get_or_create.return_value = {
        "channel": "web_pwa",
        "history": [],
    }
    mock_deps["session_mgr"].get_history.return_value = []
    mock_deps["cost_tracker"].is_budget_exceeded.return_value = False
    mock_deps["context_assembler"].assemble.return_value = "You are Alfred."
    mock_deps["tool_registry"].get_tools.return_value = []
    mock_deps["context_reader"].get_rendered_context.return_value = ""

    engine = ConsciousEngine(**mock_deps)

    request = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        content_type="text",
        content="Hello",
    )

    with patch.object(engine, "_call_claude", new_callable=AsyncMock) as mock_claude:
        mock_claude.return_value = ("Good evening, sir.", [], 100, 50)
        response = await engine.process_request(request)

    assert isinstance(response, AlfredResponse)
    assert response.text == "Good evening, sir."


@pytest.mark.asyncio
async def test_budget_exceeded_returns_fallback(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    mock_deps["identity_gate"].resolve.return_value = IdentityResult(
        identity="sir",
        confidence=0.99,
        method="webauthn",
        factors=["webauthn"],
        risk_clearance="high",
    )
    mock_deps["cost_tracker"].is_budget_exceeded.return_value = True

    engine = ConsciousEngine(**mock_deps)

    request = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        content_type="text",
        content="Good morning",
    )

    response = await engine.process_request(request)
    assert isinstance(response, AlfredResponse)
    assert "budget" in response.text.lower() or "reduced" in response.text.lower()
