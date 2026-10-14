"""Tools sovereign services register in ``alfred:tool_registry``, routed as actions.

Offered by wire name, scoped by identity (a guest sees ``benign`` tools only), validated
against the tool's own JSON Schema, and dispatched as one ``ActionRequest`` through the
domain router. A failed action reaches the model as a failed tool result with the
service's text, which spends no retry.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic_ai import RunContext, ToolDefinition, ToolFailed
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from bus.schemas.events import ActionRequest
from core.conscious.deps import TurnDeps
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.names import WireNameError, error_once, report_collision, wire_name
from core.conscious.tools.scope import allowed, refusal
from core.conscious.tools.validation import ANY_OBJECT, check_arguments, compile_schema
from core.triggers.models import TRIGGER_ENGINE_SOURCE

if TYPE_CHECKING:
    from core.reflex.tool_registry import ToolInfo

REASON_PARAM: Final = "reason"
REASON_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "description": (
        "One sentence, addressed to the user, saying why this action is needed. "
        "It is shown on the confirmation prompt."
    ),
}
SOURCE: Final = "conscious-engine"


@dataclass
class RegistryToolset(AbstractToolset[TurnDeps]):
    """The turn's registry tools (``TurnDeps.registry_tools``).

    *skip_services* are registrations another toolset serves in-process: the triggers
    process registers ``TriggerFeature``, which ``TriggerToolset`` offers directly.
    """

    skip_services: frozenset[str] = frozenset({TRIGGER_ENGINE_SOURCE})

    @property
    def id(self) -> str | None:
        return "registry"

    async def get_tools(self, ctx: RunContext[TurnDeps]) -> dict[str, ToolsetTool[TurnDeps]]:
        tools: dict[str, ToolsetTool[TurnDeps]] = {}
        owners: dict[str, str] = {}
        for info in ctx.deps.registry_tools:
            if info.target_service in self.skip_services or not allowed(info.risk, ctx.deps.who):
                continue
            built = self._build(info)
            if built is None:
                continue
            name = built.tool_def.name
            if name in tools:
                report_collision(name, owners[name], info.name)
                continue
            tools[name] = built
            owners[name] = info.name
        return tools

    def _build(self, info: ToolInfo) -> ToolsetTool[TurnDeps] | None:
        try:
            name = wire_name(info.name)
        except WireNameError as exc:
            error_once(("wire-name", info.name), "Tool %r is not offered: %s", info.name, exc)
            return None
        validator = compile_schema(info.name, info.input_schema)
        if validator is None:
            return None
        # A critical tool is offered an optional `reason` for the confirmation prompt,
        # unless it declares its own, which then belongs to the service.
        strip_reason = info.risk == "critical" and REASON_PARAM not in info.parameter_names
        schema = copy.deepcopy(info.input_schema)
        if strip_reason:
            schema.setdefault("properties", {})[REASON_PARAM] = dict(REASON_SCHEMA)

        def validate(ctx: RunContext[TurnDeps], **args: Any) -> None:
            if strip_reason:
                args.pop(REASON_PARAM, None)
            check_arguments(validator, args)

        return ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name=name,
                description=info.description,
                parameters_json_schema=schema,
                metadata={
                    "registry_name": info.name,
                    "target_service": info.target_service,
                    "risk": info.risk,
                    "strip_reason": strip_reason,
                },
            ),
            max_retries=TOOL_RETRIES,
            args_validator=ANY_OBJECT,
            args_validator_func=validate,
        )

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[TurnDeps],
        tool: ToolsetTool[TurnDeps],
    ) -> Any:
        metadata = tool.tool_def.metadata or {}
        if not allowed(str(metadata["risk"]), ctx.deps.who):
            raise ToolFailed(refusal(name))
        params = dict(tool_args)
        reason = params.pop(REASON_PARAM, None) if metadata["strip_reason"] else None
        action = ActionRequest(
            source=SOURCE,
            target_service=str(metadata["target_service"]),
            tool_name=str(metadata["registry_name"]),
            parameters=params,
            reason=reason if isinstance(reason, str) and reason.strip() else None,
        )
        result = await ctx.deps.services.router.route(action)
        if result.status != "success":
            raise ToolFailed(result.error or f"{name} failed")
        return result.result if result.result is not None else {"status": "success"}
