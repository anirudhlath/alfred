"""System 2, the conversational agent (spec, component 2).

Built once per process, with no model: the engine passes the resolved model on every run.
Its one toolset is the catalog, so every tool reaches the model under one name mapping
(``tools.names.wire_name``) and is dispatched by that same name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai_harness.repair_tool_arguments import RepairToolArguments

from core.conscious.deps import TurnDeps
from core.conscious.tools.attention import attention_toolset
from core.conscious.tools.catalog import CatalogToolset
from core.conscious.tools.integrations import integration_toolset
from core.conscious.tools.memory import memory_toolset
from core.conscious.tools.registry import RegistryToolset
from core.conscious.tools.triggers import TriggerToolset
from shared.agents import instrumentation, role_headers
from shared.llm_roles import SYSTEM2

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.toolsets import AbstractToolset

    from core.conscious.context_assembler import ContextAssembler
    from core.triggers.feature import TriggerFeature

SYSTEM2_NAME: Final = "system2"
# Model requests per turn; replaces the old loop's MAX_ITERATIONS.
REQUEST_LIMIT: Final = 10
OUTPUT_RETRIES: Final = 2


def system2_toolsets(trigger_feature: TriggerFeature | None) -> list[AbstractToolset[TurnDeps]]:
    """System 2's toolsets in priority order: in-process tools first, so a service's
    registration can never take an internal tool's name."""
    toolsets: list[AbstractToolset[TurnDeps]] = []
    if trigger_feature is not None:
        toolsets.append(TriggerToolset(trigger_feature))
    toolsets += [memory_toolset(), attention_toolset(), integration_toolset(), RegistryToolset()]
    return toolsets


def build_system2_agent(
    *,
    toolsets: Sequence[AbstractToolset[TurnDeps]],
    assembler: ContextAssembler,
    max_tokens: int,
    timeout_s: float,
) -> Agent[TurnDeps, str]:
    """The System 2 agent: *toolsets* in one catalog, instructions assembled per turn."""
    agent: Agent[TurnDeps, str] = Agent(
        None,
        name=SYSTEM2_NAME,
        deps_type=TurnDeps,
        output_type=str,
        toolsets=[CatalogToolset(list(toolsets))],
        retries=OUTPUT_RETRIES,
        model_settings=ModelSettings(
            max_tokens=max_tokens, timeout=timeout_s, extra_headers=role_headers(SYSTEM2)
        ),
        capabilities=[RepairToolArguments(), instrumentation()],
        defer_model_check=True,
    )

    @agent.instructions
    def _instructions(ctx: RunContext[TurnDeps]) -> str:
        d = ctx.deps
        return assembler.assemble(
            d.identity,
            integrations_available=bool(d.integration_tools),
            now=d.now,
            relevant_context=d.recall or None,
            channel=d.channel,
            content_type=d.content_type,
            area=d.area,
            tz_name=d.tz_name,
            routine_hint=d.routine_hint,
        )

    return agent
