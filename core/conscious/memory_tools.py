"""Internal memory tools for deliberate recall during agentic reasoning.

These are in-process tools, served to System 2 by ``core.conscious.tools.memory``
like the integration and trigger tools. They are NOT registered in the Redis
ToolRegistry (that's for external services).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.memory.context_index import ContextIndexManager
    from core.reflex.context_reader import ContextReader


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
