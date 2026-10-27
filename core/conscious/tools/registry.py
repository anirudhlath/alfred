"""Tools sovereign services register in ``alfred:tool_registry``, routed as actions.

Offered by wire name, scoped by identity (a guest sees ``benign`` tools only), validated
against the tool's own JSON Schema, and dispatched as one ``ActionRequest`` through the
domain router. A failed action reaches the model as a failed tool result with the
service's text, which spends no retry.

A ``critical`` tool waits for the owner (#317 PR 5): once its arguments are valid, the
validator raises ``ApprovalRequired``, which ends the run in ``DeferredToolRequests`` before
the call is recorded or dispatched, and the engine parks the run. Only a resumed run whose
call the owner approved (``ctx.tool_call_approved``) gets past it; that call is routed
``confirmed`` and its outcome published for the client that showed the approval. The model
never sees an approval id.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic_ai import ApprovalRequired, RunContext, ToolDefinition, ToolFailed
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from bus.schemas.events import ActionRequest, ActionResult
from core.conscious.deps import TurnDeps
from core.conscious.effects import SIDE_EFFECT
from core.conscious.tools.catalog import TOOL_RETRIES
from core.conscious.tools.names import WireNameError, error_once, report_collision, wire_name
from core.conscious.tools.scope import Refused, allowed
from core.conscious.tools.validation import ANY_OBJECT, check_arguments, compile_schema
from core.reflex.tool_registry import REFLEX_AUDIENCE
from core.routing.action_results import publish_action_result
from core.routing.domain_router import CONFIRMATION_REQUIRED_PREFIX
from core.triggers.models import TRIGGER_ENGINE_SOURCE

if TYPE_CHECKING:
    from core.reflex.tool_registry import ToolInfo

logger = logging.getLogger(__name__)

REASON_PARAM: Final = "reason"
REASON_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "description": (
        "One sentence, addressed to the user, saying why this action is needed. "
        "It is shown on the confirmation prompt."
    ),
}
SOURCE: Final = "conscious-engine"
# What the Door is told when an approved call's route raises. The exception's text can
# carry hosts, ports or paths, so it stays in the log.
NO_RESULT: Final = "No result came back from the service."
# What the model reads when the router parks an action itself: no id, so it has nothing to
# hand to anything that approves.
INTERCEPTED: Final = (
    "This action needs the owner's confirmation; a request was sent to their phone."
)


def _split_reason(args: dict[str, Any], strip_reason: bool) -> tuple[dict[str, Any], str | None]:
    """The service's parameters, and the confirmation prompt's reason when the tool was
    offered one: a non-blank string, else None."""
    params = dict(args)
    reason = params.pop(REASON_PARAM, None) if strip_reason else None
    return params, reason if isinstance(reason, str) and reason.strip() else None


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
        checked = info.input_schema
        # The owner approves every parameter a critical call carries, so it may carry only
        # the ones its tool declares, unless the schema itself allows more (#397).
        closed = info.risk == "critical" and "additionalProperties" not in checked
        if closed:
            checked = {**checked, "additionalProperties": False}
        validator = compile_schema(info.name, checked)
        if validator is None:
            return None
        # A critical tool is offered an optional `reason` for the confirmation prompt,
        # unless it declares its own, which then belongs to the service.
        strip_reason = info.risk == "critical" and REASON_PARAM not in info.parameter_names
        schema = copy.deepcopy(checked)
        if strip_reason:
            schema.setdefault("properties", {})[REASON_PARAM] = dict(REASON_SCHEMA)

        def validate(ctx: RunContext[TurnDeps], **args: Any) -> None:
            params, reason = _split_reason(args, strip_reason)
            check_arguments(validator, params)
            # Re-run when the run resumes, so the owner's approval is what lets it through.
            if info.risk == "critical" and not ctx.tool_call_approved:
                raise ApprovalRequired(
                    metadata={
                        "tool_name": info.name,
                        "target_service": info.target_service,
                        "parameters": params,
                        "reason": reason,
                    }
                )

        return ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name=name,
                description=info.description,
                parameters_json_schema=schema,
                # Only the reflex audience's everyday verbs are always loaded; the model
                # finds the rest by searching (``tools.search``).
                defer_loading=info.audience != REFLEX_AUDIENCE,
                metadata={
                    "registry_name": info.name,
                    "target_service": info.target_service,
                    "risk": info.risk,
                    "strip_reason": strip_reason,
                    # Every registry tool reaches another service as an action.
                    SIDE_EFFECT: True,
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
            raise Refused(name)
        params, reason = _split_reason(tool_args, bool(metadata["strip_reason"]))
        registry_name = str(metadata["registry_name"])
        action = ActionRequest(
            source=SOURCE,
            target_service=str(metadata["target_service"]),
            tool_name=registry_name,
            parameters=params,
            reason=reason,
            confirmed=ctx.tool_call_approved,
        )
        try:
            result = await ctx.deps.services.router.route(action)
        except Exception:
            # The catalog turns this into a plain failure for the model; the Door waiting
            # on the approval still needs an outcome, or it waits until it times out.
            if ctx.tool_call_approved:
                await _publish_outcome(ctx.deps, registry_name, status="error", error=NO_RESULT)
            raise
        if ctx.tool_call_approved:
            await _publish_outcome(
                ctx.deps,
                registry_name,
                status=result.status,
                result=result.result,
                error=result.error,
            )
        if result.status != "success":
            if result.error and result.error.startswith(CONFIRMATION_REQUIRED_PREFIX):
                raise ToolFailed(INTERCEPTED)
            raise ToolFailed(result.error or f"{name} failed")
        return result.result if result.result is not None else {"status": "success"}


async def _publish_outcome(
    deps: TurnDeps,
    registry_name: str,
    *,
    status: Literal["success", "error"],
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Publish an approved call's outcome under its approval, for the client that showed
    it. The action has already run: a lost publish is logged, never the call's failure."""
    outcome = ActionResult(
        source=SOURCE,
        request_id=deps.approval_id or deps.request_id,
        tool_name=registry_name,
        status=status,
        result=result,
        error=error,
    )
    try:
        await publish_action_result(deps.services.redis, outcome)
    except Exception:
        logger.warning(
            "Could not publish the outcome of approved call %s", registry_name, exc_info=True
        )
