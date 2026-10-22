"""Attention tools, for the verified user.

There is no tool that approves anything: a critical call waits for the owner outside the
model (``core.conscious.approvals``).
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, Tool
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from core.conscious.deps import TurnDeps  # noqa: TC001 — Pydantic AI reads tool signatures
from core.conscious.effects import SIDE_EFFECT
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.sir_only import SirOnlyToolset
from core.reflex import attention


async def attention_add(ctx: RunContext[TurnDeps], domain: str, entity_id: str) -> dict[str, str]:
    """Add an entity to the Reflex attention set so ambient state changes for it are
    processed by the fast reflex engine.

    Args:
        domain: Alfred domain, e.g. 'home'.
        entity_id: e.g. 'sensor.dryer_power'.
    """
    await attention.attention_add(ctx.deps.services.redis, domain, entity_id)
    return {"status": "added", "entity_id": entity_id}


async def attention_remove(
    ctx: RunContext[TurnDeps], domain: str, entity_id: str
) -> dict[str, str]:
    """Remove an entity from the Reflex attention set (it stays visible to triggers and
    context — only ambient SLM reactions stop).

    Args:
        domain: Alfred domain, e.g. 'home'.
        entity_id: e.g. 'light.bedroom'.
    """
    await attention.attention_remove(ctx.deps.services.redis, domain, entity_id)
    return {"status": "removed", "entity_id": entity_id}


async def attention_list(ctx: RunContext[TurnDeps], domain: str) -> dict[str, Any]:
    """List the entities currently in the Reflex attention set for a domain.

    Args:
        domain: Alfred domain, e.g. 'home'.
    """
    entities = await attention.attention_list(ctx.deps.services.redis, domain)
    return {"entities": entities, "count": len(entities)}


ATTENTION_FUNCTIONS: FunctionToolset[TurnDeps] = FunctionToolset(
    [
        Tool(attention_add, metadata={SIDE_EFFECT: True}),
        Tool(attention_remove, metadata={SIDE_EFFECT: True}),
        attention_list,
    ],
    max_retries=TOOL_RETRIES,
    id="attention",
)


def attention_toolset() -> AbstractToolset[TurnDeps]:
    """The attention tools, for the verified user."""
    return SirOnlyToolset(ATTENTION_FUNCTIONS)
