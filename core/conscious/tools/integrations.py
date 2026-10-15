"""Integration tools: one per adapter capability (weather, calendar, health, finance).

The capabilities are read once per turn, before the run (``fetch_integration_tools``),
and a call is executed by the integration and capability its tool was built from, never
by parsing the tool's name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext, ToolDefinition, ToolFailed
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from core.conscious.deps import TurnDeps
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.names import WireNameError, error_once, report_collision, wire_name
from core.conscious.tools.sir_only import SirOnlyToolset
from core.conscious.tools.validation import ANY_OBJECT, check_arguments, compile_schema
from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntegrationTool:
    """One capability as a tool: its wire name, where it runs, and its schema."""

    name: str
    integration: str
    capability: str
    description: str
    schema: dict[str, Any]


async def fetch_integration_tools() -> list[IntegrationTool]:
    """Every capability of every available integration, as tools."""
    tools: list[IntegrationTool] = []
    for name in IntegrationRegistry.available():
        try:
            capabilities = await IntegrationRegistry.get(name).get_capabilities()
        except Exception:
            logger.warning("Failed to get capabilities for integration %s", name, exc_info=True)
            continue
        for cap in capabilities:
            identity = f"integration_{name}_{cap.name}"
            try:
                tool_name = wire_name(identity)
            except WireNameError as exc:
                error_once(("wire-name", identity), "Tool %r is not offered: %s", identity, exc)
                continue
            properties = cap.params_schema.get("properties", {})
            schema = {
                "type": "object",
                "properties": properties,
                "required": [
                    k for k, v in properties.items() if isinstance(v, dict) and "default" not in v
                ],
            }
            tools.append(
                IntegrationTool(tool_name, name, cap.name, f"[{name}] {cap.description}", schema)
            )
    return tools


def _checker(validator: Draft202012Validator) -> Any:
    def validate(ctx: RunContext[TurnDeps], **args: Any) -> None:
        check_arguments(validator, args)

    return validate


@dataclass
class IntegrationToolset(AbstractToolset[TurnDeps]):
    """The turn's integration tools (``TurnDeps.integration_tools``)."""

    @property
    def id(self) -> str | None:
        return "integrations"

    async def get_tools(self, ctx: RunContext[TurnDeps]) -> dict[str, ToolsetTool[TurnDeps]]:
        tools: dict[str, ToolsetTool[TurnDeps]] = {}
        for t in ctx.deps.integration_tools:
            validator = compile_schema(t.name, t.schema)
            if validator is None:
                continue
            if t.name in tools:
                report_collision(t.name, t.name, f"{t.integration}.{t.capability}")
                continue
            tools[t.name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=t.name,
                    description=t.description,
                    parameters_json_schema=t.schema,
                    metadata={"integration": t.integration, "capability": t.capability},
                ),
                max_retries=TOOL_RETRIES,
                args_validator=ANY_OBJECT,
                args_validator_func=_checker(validator),
            )
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[TurnDeps],
        tool: ToolsetTool[TurnDeps],
    ) -> Any:
        metadata = tool.tool_def.metadata or {}
        adapter = IntegrationRegistry.get(str(metadata["integration"]))
        result = await adapter.execute(
            IntegrationRequest(action=str(metadata["capability"]), params=dict(tool_args))
        )
        if "error" in result.data:
            raise ToolFailed(str(result.data["error"]))
        return result.data


def integration_toolset() -> AbstractToolset[TurnDeps]:
    """The integration tools, for the verified user."""
    return SirOnlyToolset(IntegrationToolset())
