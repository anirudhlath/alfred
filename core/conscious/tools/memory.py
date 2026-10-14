"""Memory tools: deliberate recall and live device state, for the verified user."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, ToolDefinition, ToolFailed
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from core.conscious.deps import TurnDeps  # noqa: TC001 — Pydantic AI reads tool signatures
from core.conscious.memory_tools import get_live_state, recall_memories
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.sir_only import SirOnlyToolset


async def memory_recall_memories(
    ctx: RunContext[TurnDeps],
    query: str,
    types: list[str] | None = None,
    since_days_ago: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search Alfred's memory for relevant information about past events, preferences,
    or patterns.

    Args:
        query: Natural language search query.
        types: Filter by type: episodic, semantic, routine.
        since_days_ago: Only include entries from last N days.
        limit: Max results.
    """
    index = ctx.deps.services.context_index
    if index is None:
        raise ToolFailed("Memory is not available")
    return await recall_memories(
        index, query, types=types, since_days_ago=since_days_ago, limit=limit
    )


async def memory_get_live_state(
    ctx: RunContext[TurnDeps], entities: list[str] | None = None
) -> dict[str, Any]:
    """Get current Home Assistant device state. `available` is false when Alfred has no
    live state (Home Assistant disconnected) — that is not the same as nothing being on.

    Args:
        entities: Entity IDs or glob patterns like light.*.
    """
    return await get_live_state(ctx.deps.services.context_reader, entities)


MEMORY_FUNCTIONS: FunctionToolset[TurnDeps] = FunctionToolset(
    [memory_recall_memories, memory_get_live_state], max_retries=TOOL_RETRIES, id="memory"
)


def _has_memory(ctx: RunContext[TurnDeps], _tool: ToolDefinition) -> bool:
    return ctx.deps.services.context_index is not None


def memory_toolset() -> AbstractToolset[TurnDeps]:
    """The memory tools, for the verified user, when the process has a memory index."""
    return SirOnlyToolset(MEMORY_FUNCTIONS.filtered(_has_memory))
