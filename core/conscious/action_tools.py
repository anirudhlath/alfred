"""Internal action tools — pending-action confirmation + attention primitives.

Dispatched in-process by the Conscious Engine, following the same pattern as
memory tools (core/conscious/memory_tools.py). NOT registered in the Redis
ToolRegistry. Exposed to "sir" only — guests must not confirm critical
actions or reshape Reflex attention.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.reflex.attention import attention_add, attention_list, attention_remove
from core.routing.pending import confirm_pending_action as _confirm_pending

if TYPE_CHECKING:
    from shared.types import AioRedis

ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {"confirm_pending_action", "attention_add", "attention_remove", "attention_list"}
)

ACTION_TOOLS_MANIFEST: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "confirm_pending_action",
            "description": (
                "Confirm a pending critical action (e.g. unlocking a door) that is "
                "awaiting user approval. Only call this when the user has explicitly "
                "agreed in this conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The pending action id from the confirmation notification",
                    },
                },
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attention_add",
            "description": (
                "Add an entity to the Reflex attention set so ambient state changes "
                "for it are processed by the fast reflex engine"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Alfred domain, e.g. 'home'"},
                    "entity_id": {"type": "string", "description": "e.g. 'sensor.dryer_power'"},
                },
                "required": ["domain", "entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attention_remove",
            "description": (
                "Remove an entity from the Reflex attention set (it stays visible to "
                "triggers and context — only ambient SLM reactions stop)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Alfred domain, e.g. 'home'"},
                    "entity_id": {"type": "string", "description": "e.g. 'light.bedroom'"},
                },
                "required": ["domain", "entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attention_list",
            "description": "List the entities currently in the Reflex attention set for a domain",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Alfred domain, e.g. 'home'"},
                },
                "required": ["domain"],
            },
        },
    },
]


async def dispatch_action_tool(tool_name: str, params: dict[str, Any], redis: AioRedis) -> str:
    """Dispatch an internal action tool call. Returns a JSON string result."""
    match tool_name:
        case "confirm_pending_action":
            request_id = str(params.get("request_id", ""))
            confirmed = await _confirm_pending(redis, request_id)
            if confirmed is None:
                return json.dumps(
                    {"error": f"No pending action '{request_id}' — it may have expired"}
                )
            return json.dumps(
                {
                    "status": "confirmed",
                    "request_id": request_id,
                    "tool_name": confirmed.tool_name,
                }
            )
        case "attention_add":
            await attention_add(redis, str(params["domain"]), str(params["entity_id"]))
            return json.dumps({"status": "added", "entity_id": params["entity_id"]})
        case "attention_remove":
            await attention_remove(redis, str(params["domain"]), str(params["entity_id"]))
            return json.dumps({"status": "removed", "entity_id": params["entity_id"]})
        case "attention_list":
            entities = await attention_list(redis, str(params.get("domain", "home")))
            return json.dumps({"entities": entities, "count": len(entities)})
        case _:
            return json.dumps({"error": f"Unknown action tool: {tool_name}"})
