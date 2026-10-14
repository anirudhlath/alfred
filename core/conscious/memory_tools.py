"""Internal memory tools for deliberate recall during agentic reasoning.

These are in-process tools dispatched directly by the Conscious Engine,
following the same pattern as integration and trigger tools. They are NOT
registered in the Redis ToolRegistry (that's for external services).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.memory.context_index import ContextIndexManager
    from core.reflex.context_reader import ContextReader

MEMORY_TOOL_PREFIX = "memory_"

MEMORY_TOOLS_MANIFEST: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_recall_memories",
            "description": (
                "Search Alfred's memory for relevant information about past events, "
                "preferences, or patterns"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by type: episodic, semantic, routine",
                    },
                    "since_days_ago": {
                        "type": "integer",
                        "description": "Only include entries from last N days",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get_live_state",
            "description": (
                "Get current Home Assistant device state. `available` is false when "
                "Alfred has no live state (Home Assistant disconnected) — that is not "
                "the same as nothing being on"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Entity IDs or glob patterns like light.*",
                    },
                },
            },
        },
    },
]


async def dispatch_memory_tool(
    tool_name: str,
    params: dict[str, Any],
    context_index: ContextIndexManager,
    context_reader: ContextReader,
) -> str:
    """Dispatch a memory tool call. Returns JSON string result."""
    if tool_name == "memory_recall_memories":
        return json.dumps(
            await recall_memories(
                context_index,
                params.get("query", ""),
                types=params.get("types"),
                since_days_ago=params.get("since_days_ago"),
                limit=params.get("limit", 10),
            )
        )
    elif tool_name == "memory_get_live_state":
        return json.dumps(await get_live_state(context_reader, params.get("entities")))
    else:
        return json.dumps({"error": f"Unknown memory tool: {tool_name}"})


async def recall_memories(
    index: ContextIndexManager,
    query: str,
    *,
    types: list[str] | None = None,
    since_days_ago: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search hot memory and the cold archive, compressed entries included.

    Counts as using the memories it returns. The filters go to the search, so the limit
    counts only memories that match.
    """
    results = await index.recall(
        query,
        limit=limit,
        types=types,
        since=datetime.now(UTC) - timedelta(days=since_days_ago) if since_days_ago else None,
    )
    formatted = [
        {
            "content": r.content,
            "type": r.metadata.type,
            "source": r.metadata.source,
            "score": round(r.score, 3),
        }
        for r in results
    ]
    return {"memories": formatted, "count": len(formatted)}


async def get_live_state(
    reader: ContextReader, entities: list[str] | None = None
) -> dict[str, Any]:
    """The current state of *entities* (ids or globs), or every entity.

    ``available`` is False when Alfred has no live state at all, which is not the same as
    nothing being on.
    """
    states = await reader.get_entity_states(patterns=entities)
    if states is None:
        return {"available": False, "entities": []}
    return {"available": True, "entities": states}
