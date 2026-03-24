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
    from core.memory.embedding_provider import EmbeddingProvider
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
            "description": "Get current Home Assistant device state",
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
    embedder: EmbeddingProvider,
) -> str:
    """Dispatch a memory tool call. Returns JSON string result."""
    if tool_name == "memory_recall_memories":
        return await _recall_memories(params, context_index, embedder)
    elif tool_name == "memory_get_live_state":
        return await _get_live_state(params, context_reader)
    else:
        return json.dumps({"error": f"Unknown memory tool: {tool_name}"})


async def _recall_memories(
    params: dict[str, Any],
    context_index: ContextIndexManager,
    embedder: EmbeddingProvider,
) -> str:
    query: str = params.get("query", "")
    limit: int = params.get("limit", 10)

    query_emb = await embedder.embed(query)
    results = await context_index.search(
        query_embedding=query_emb,
        limit=limit,
        include_compressed=True,  # Deliberate recall includes compressed
    )

    # Filter by type if specified
    types: list[str] | None = params.get("types")
    if types:
        results = [r for r in results if r.metadata.type in types]

    # Filter by time if specified
    since_days: int | None = params.get("since_days_ago")
    if since_days:
        cutoff = (datetime.now(UTC) - timedelta(days=since_days)).timestamp()
        results = [
            r for r in results if r.metadata.timestamp >= cutoff or r.metadata.timestamp == 0.0
        ]

    formatted = [
        {
            "content": r.content,
            "type": r.metadata.type,
            "source": r.metadata.source,
            "score": round(r.score, 3),
        }
        for r in results
    ]
    return json.dumps({"memories": formatted, "count": len(formatted)})


async def _get_live_state(
    params: dict[str, Any],
    context_reader: ContextReader,
) -> str:
    states = await context_reader.get_entity_states(
        patterns=params.get("entities"),
    )
    return json.dumps({"entities": states})
