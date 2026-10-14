"""Confirmation and attention tools, for the verified user (spec ruling R1)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, Tool, ToolFailed
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from core.conscious.deps import TurnDeps  # noqa: TC001 — Pydantic AI reads tool signatures
from core.conscious.effects import SIDE_EFFECT
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.sir_only import SirOnlyToolset
from core.reflex import attention
from core.routing.pending import confirm_pending_action as confirm_pending


async def confirm_pending_action(ctx: RunContext[TurnDeps], request_id: str) -> dict[str, str]:
    """Confirm a pending critical action (e.g. unlocking a door) that is awaiting user
    approval. Only call this when the user has explicitly agreed in this conversation.

    Args:
        request_id: The pending action id from the confirmation notification.
    """
    confirmed = await confirm_pending(ctx.deps.services.redis, request_id)
    if confirmed is None:
        raise ToolFailed(f"No pending action '{request_id}' — it may have expired")
    return {"status": "confirmed", "request_id": request_id, "tool_name": confirmed.tool_name}


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
        confirm_pending_action,
        Tool(attention_add, metadata={SIDE_EFFECT: True}),
        Tool(attention_remove, metadata={SIDE_EFFECT: True}),
        attention_list,
    ],
    max_retries=TOOL_RETRIES,
    id="attention",
)


def attention_toolset() -> AbstractToolset[TurnDeps]:
    """The confirmation and attention tools, for the verified user."""
    return SirOnlyToolset(ATTENTION_FUNCTIONS)
