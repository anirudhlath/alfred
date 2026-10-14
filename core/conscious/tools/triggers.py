"""Trigger tools, served in-process from ``TriggerFeature`` (spec ruling R7).

A custom toolset rather than a ``FunctionToolset``: it keeps the runtime-enriched
``create_trigger`` description and ``ToolMeta.validate_arguments``' lax coercion
(``"false"`` becomes ``False``), the same validation ``AlfredClient.dispatch`` runs for a
service's tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry, RunContext, ToolDefinition, ToolFailed
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from core.conscious.deps import TurnDeps
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.names import wire_name
from core.conscious.tools.scope import allowed, refusal
from core.conscious.tools.validation import ANY_OBJECT
from sdk.alfred_sdk.feature import ToolArgumentError

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.triggers.feature import TriggerFeature
    from sdk.alfred_sdk.feature import ToolMeta


def _validator(meta: ToolMeta) -> Callable[..., None]:
    def validate(ctx: RunContext[TurnDeps], **args: Any) -> None:
        try:
            meta.validate_arguments(args)
        except ToolArgumentError as exc:
            raise ModelRetry(str(exc)) from exc

    return validate


@dataclass
class TriggerToolset(AbstractToolset[TurnDeps]):
    """``TriggerFeature``'s tools, read once when the toolset is built."""

    feature: TriggerFeature
    _metas: dict[str, ToolMeta] = field(init=False, repr=False)
    _tools: dict[str, ToolsetTool[TurnDeps]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._metas = {}
        self._tools = {}
        for meta in self.feature.get_tools():
            # These names are ours: one that has no wire name fails at startup.
            name = wire_name(meta.name)
            self._metas[name] = meta
            self._tools[name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=name,
                    description=meta.description,
                    parameters_json_schema=meta.input_schema,
                    metadata={"registry_name": meta.name, "risk": meta.risk},
                ),
                max_retries=TOOL_RETRIES,
                args_validator=ANY_OBJECT,
                args_validator_func=_validator(meta),
            )

    @property
    def id(self) -> str | None:
        return "triggers"

    async def get_tools(self, ctx: RunContext[TurnDeps]) -> dict[str, ToolsetTool[TurnDeps]]:
        return {
            name: tool
            for name, tool in self._tools.items()
            if allowed(self._metas[name].risk, ctx.deps.who)
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[TurnDeps],
        tool: ToolsetTool[TurnDeps],
    ) -> Any:
        meta = self._metas[name]
        if not allowed(meta.risk, ctx.deps.who):
            raise ToolFailed(refusal(name))
        kwargs = meta.validate_arguments(tool_args)
        method = getattr(self.feature, meta.name.removeprefix(f"{self.feature.feature_name}."))
        result: Any = await method(**kwargs)
        if isinstance(result, dict) and "error" in result:
            raise ToolFailed(str(result["error"]))
        return result
