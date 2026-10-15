"""The catalog: System 2's one toolset, joining the others under one name mapping.

Pydantic AI's ``CombinedToolset`` refuses a run when two toolsets offer one name. The
catalog keeps the first and logs the second once, so one bad registration cannot take
System 2 down. It also records each call it dispatches (``TurnDeps.actions_taken``) and
turns an unexpected exception into a failed tool result the model can read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from typing import Any, Final

from pydantic_ai import ModelRetry, RunContext, ToolFailed
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from core.conscious.deps import TurnDeps
from core.conscious.tools.names import report_collision

logger = logging.getLogger(__name__)

# Retries per tool. Pydantic AI's default of 1 is low for a local model's first attempt.
TOOL_RETRIES: Final = 2
# How an empty list result reaches the model.
EMPTY_LIST: Final = "[]"
_PASS_THROUGH = (ModelRetry, ToolFailed, ApprovalRequired, CallDeferred)


@dataclass(kw_only=True)
class CatalogTool(ToolsetTool[TurnDeps]):
    """A tool as the catalog offers it, remembering which toolset dispatches it."""

    source_toolset: AbstractToolset[TurnDeps] = field(repr=False)
    source_tool: ToolsetTool[TurnDeps] = field(repr=False)


def _identity(tool: ToolsetTool[TurnDeps], name: str) -> str:
    """The tool's registry name when it has one, else its wire name."""
    metadata = tool.tool_def.metadata or {}
    registry_name = metadata.get("registry_name")
    return registry_name if isinstance(registry_name, str) else name


@dataclass
class CatalogToolset(CombinedToolset[TurnDeps]):
    """Every System 2 toolset, in priority order: the first to offer a wire name owns it."""

    @property
    def id(self) -> str | None:
        return "catalog"

    async def get_tools(self, ctx: RunContext[TurnDeps]) -> dict[str, ToolsetTool[TurnDeps]]:
        tools: dict[str, ToolsetTool[TurnDeps]] = {}
        for toolset in self.toolsets:
            for name, tool in (await toolset.get_tools(ctx)).items():
                if name in tools:
                    kept = _identity(tools[name], name)
                    report_collision(name, kept, _identity(tool, name))
                    continue
                base = {f.name: getattr(tool, f.name) for f in fields(ToolsetTool) if f.init}
                tools[name] = CatalogTool(**base, source_toolset=toolset, source_tool=tool)
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[TurnDeps],
        tool: ToolsetTool[TurnDeps],
    ) -> Any:
        if not isinstance(tool, CatalogTool):
            raise TypeError(f"{name} was not listed by the catalog")
        ctx.deps.actions_taken.append(_identity(tool, name))
        try:
            result = await tool.source_toolset.call_tool(name, tool_args, ctx, tool.source_tool)
        except _PASS_THROUGH:
            raise
        except Exception as exc:
            # The exception's text can carry hosts, ports or paths: log it, and tell the
            # model only that the call failed.
            logger.warning("Tool %s failed", name, exc_info=True)
            raise ToolFailed(f"{name} failed") from exc
        # Pydantic AI reads a list as multi-part content and renders an empty one as an
        # empty tool result; "nothing found" should reach the model as "[]".
        return EMPTY_LIST if result == [] else result
