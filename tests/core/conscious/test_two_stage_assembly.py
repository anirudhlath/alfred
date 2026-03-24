"""Tests for two-stage context assembly: involuntary recall in the Conscious Engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.engine import ConsciousEngine
from core.identity.schemas import IdentityResult
from core.memory.vector_store import ContextMetadata, SearchResult


def _make_request(**overrides: object) -> UserRequest:
    defaults = {
        "source": "web-pwa",
        "channel": "web_pwa",
        "session_id": "sess-1",
        "identity_claim": "sir",
        "authenticated": True,
        "content_type": "text",
        "content": "What are my lighting preferences?",
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


def _make_search_result(content: str = "test", score: float = 0.8) -> SearchResult:
    return SearchResult(
        id="sr-1",
        score=score,
        content=content,
        semantic_key=content,
        metadata=ContextMetadata(
            type="semantic",
            source="preferences.md",
            entities="",
            timestamp=0.0,
            significance=1.0,
            retrieval_count=0,
        ),
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
    deps["identity_gate"].resolve.return_value = _sir_identity()
    deps["session_mgr"].get_or_create.return_value = {"channel": "web_pwa", "history": []}
    deps["cost_tracker"].is_budget_exceeded.return_value = False
    deps["context_assembler"].assemble.return_value = "You are Alfred."
    deps["tool_registry"].get_tools.return_value = []
    deps["context_reader"].get_rendered_context.return_value = ""
    return deps


@pytest.mark.asyncio
async def test_involuntary_recall_performed(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """When embedder and context_index are provided, involuntary recall runs."""
    embedder = AsyncMock()
    embedder.embed.return_value = [0.1, 0.2, 0.3]

    context_index = AsyncMock()
    search_result = _make_search_result("Sir prefers dim lighting after 8pm")
    context_index.search.return_value = [search_result]

    engine = ConsciousEngine(
        **mock_deps,
        embedder=embedder,
        context_index=context_index,
    )

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Here are your preferences, sir.", [], 100, 50)
        await engine.process_request(_make_request())

    # Embedder was called with the user query
    embedder.embed.assert_called_once_with("What are my lighting preferences?")
    # Context index was searched
    context_index.search.assert_called_once()
    # Assembler received relevant_context
    call_kwargs = mock_deps["context_assembler"].assemble.call_args
    assert call_kwargs.kwargs.get("relevant_context") is not None
    assert len(call_kwargs.kwargs["relevant_context"]) == 1


@pytest.mark.asyncio
async def test_involuntary_recall_empty_results(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """When involuntary recall returns nothing, assembler gets relevant_context=None."""
    embedder = AsyncMock()
    embedder.embed.return_value = [0.1, 0.2, 0.3]

    context_index = AsyncMock()
    context_index.search.return_value = []

    engine = ConsciousEngine(
        **mock_deps,
        embedder=embedder,
        context_index=context_index,
    )

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Hello, sir.", [], 100, 50)
        await engine.process_request(_make_request())

    call_kwargs = mock_deps["context_assembler"].assemble.call_args
    assert call_kwargs.kwargs.get("relevant_context") is None


@pytest.mark.asyncio
async def test_involuntary_recall_failure_graceful(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Involuntary recall failure logs warning but doesn't crash."""
    embedder = AsyncMock()
    embedder.embed.side_effect = RuntimeError("embedding service down")

    context_index = AsyncMock()

    engine = ConsciousEngine(
        **mock_deps,
        embedder=embedder,
        context_index=context_index,
    )

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Hello, sir.", [], 100, 50)
        response = await engine.process_request(_make_request())

    assert isinstance(response, AlfredResponse)
    assert response.text == "Hello, sir."
    # Assembler should have been called with no relevant_context
    call_kwargs = mock_deps["context_assembler"].assemble.call_args
    assert call_kwargs.kwargs.get("relevant_context") is None


@pytest.mark.asyncio
async def test_no_involuntary_recall_without_deps(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Without embedder/context_index, involuntary recall is skipped entirely."""
    engine = ConsciousEngine(**mock_deps)

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Hello, sir.", [], 100, 50)
        response = await engine.process_request(_make_request())

    assert isinstance(response, AlfredResponse)
    # Assembler called without relevant_context
    call_kwargs = mock_deps["context_assembler"].assemble.call_args
    assert call_kwargs.kwargs.get("relevant_context") is None


@pytest.mark.asyncio
async def test_memory_tools_in_openai_tools_for_sir(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Memory tools are appended to openai_tools when context_index is available."""
    context_index = AsyncMock()

    engine = ConsciousEngine(
        **mock_deps,
        embedder=AsyncMock(),
        context_index=context_index,
    )

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Hello, sir.", [], 100, 50)
        await engine.process_request(_make_request())

    # Check the tools passed to _call_llm include memory tools
    call_args = mock_llm.call_args
    openai_tools = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("tools")
    tool_names = [t["function"]["name"] for t in openai_tools]
    assert "memory_recall_memories" in tool_names
    assert "memory_get_live_state" in tool_names
