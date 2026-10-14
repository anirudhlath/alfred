"""A wrapper that offers its toolset to the verified user only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext, ToolFailed
from pydantic_ai.toolsets import WrapperToolset

from core.conscious.deps import TurnDeps
from core.conscious.tools.scope import PERSONAL, allowed, refusal

if TYPE_CHECKING:
    from pydantic_ai.toolsets.abstract import ToolsetTool


@dataclass
class SirOnlyToolset(WrapperToolset[TurnDeps]):
    """The wrapped tools for the verified user; nothing for anyone else.

    The call-time check is defense in depth: a guest is never offered these tools, so
    the model cannot call them in a guest's run, but the check holds even if it did.
    """

    async def get_tools(self, ctx: RunContext[TurnDeps]) -> dict[str, ToolsetTool[TurnDeps]]:
        if not allowed(PERSONAL, ctx.deps.who):
            return {}
        return await super().get_tools(ctx)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[TurnDeps],
        tool: ToolsetTool[TurnDeps],
    ) -> Any:
        if not allowed(PERSONAL, ctx.deps.who):
            raise ToolFailed(refusal(name))
        return await super().call_tool(name, tool_args, ctx, tool)
