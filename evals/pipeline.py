"""Eval pipeline — orchestrates prompt building, inference, and trace capture."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.memory.reader import read_preferences
from core.reflex.decision import parse_decision
from core.reflex.prompt import build_state_change_prompt
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from evals.inference import InferFn, infer_ollama
from shared.config import AlfredConfig
from shared.tracing import TraceRecord

if TYPE_CHECKING:
    from evals.models import Scenario

_config = AlfredConfig.from_env()


class EvalContext:
    """Pre-computed state shared across scenarios in a single eval run."""

    def __init__(
        self,
        tools: list[ToolInfo],
        preferences_dir: str,
        model: str | None = None,
        *,
        infer: InferFn = infer_ollama,
    ) -> None:
        self.model = model or _config.ollama_model
        self.infer = infer
        self.preferences_text = read_preferences(preferences_dir)
        self.tools = tools
        self.valid_services = ToolRegistry.get_registered_services(tools)
        self.tools_as_dicts: list[dict[str, Any]] = [asdict(t) for t in tools]


async def run_scenario(
    scenario: Scenario,
    tools: list[ToolInfo],
    preferences_dir: str,
    model: str | None = None,
    *,
    ctx: EvalContext | None = None,
) -> TraceRecord:
    """Run a single scenario through the inference pipeline, capturing a full trace.

    Pass a pre-built ``ctx`` to avoid redundant work when running multiple scenarios.
    """
    if ctx is None:
        ctx = EvalContext(tools, preferences_dir, model)

    # Per-scenario preferences override
    if scenario.preferences_dir and scenario.preferences_dir != preferences_dir:
        preferences_text = read_preferences(scenario.preferences_dir)
    else:
        preferences_text = ctx.preferences_text

    # Context fixtures predate live state (#283) and the House section (#285). This
    # harness is being replaced by the PRD eval suite, so it renders no live state.
    resolved_model = model or ctx.model
    prompt = build_state_change_prompt(
        event=scenario.event,
        preferences=preferences_text,
        tools=ctx.tools,
        entities=None,
        now=datetime.now(UTC),
        tz_name="UTC",
    )

    # Call inference backend and measure latency
    start = time.perf_counter()
    response: dict[str, Any] = await ctx.infer(prompt, model=resolved_model)
    latency_ms = (time.perf_counter() - start) * 1000

    # Parse using the engine's real logic
    parsed_action = parse_decision(str(response.get("response", "")), ctx.tools).action

    return TraceRecord(
        trace_id=str(uuid4()),
        timestamp=datetime.now(UTC),
        model=resolved_model,
        event=scenario.event,
        preferences_text=preferences_text,
        tools=ctx.tools_as_dicts,
        prompt=prompt,
        raw_response=str(response.get("response", "")),
        parsed_action=parsed_action,
        latency_ms=latency_ms,
        prompt_tokens=int(response.get("prompt_tokens", 0)),
        completion_tokens=int(response.get("completion_tokens", 0)),
    )
