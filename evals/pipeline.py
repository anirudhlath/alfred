"""Eval pipeline — orchestrates prompt building, inference, and trace capture."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from core.reflex import ollama_client
from core.reflex.engine import ReflexEngine
from core.reflex.memory_reader import read_preferences
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from shared.config import AlfredConfig
from shared.tracing import TraceRecord

if TYPE_CHECKING:
    from evals.models import Scenario

_config = AlfredConfig.from_env()


async def run_scenario(
    scenario: Scenario,
    tools: list[ToolInfo],
    preferences_dir: str,
    model: str | None = None,
) -> TraceRecord:
    """Run a single scenario through the inference pipeline, capturing a full trace."""
    model = model or _config.ollama_model

    # Use a throwaway engine instance for prompt building / response parsing
    # (no Redis needed — we pass tools directly)
    engine = ReflexEngine(
        preferences_dir=preferences_dir,
        tool_registry=ToolRegistry(redis=None),
    )

    # Build prompt using the engine's real logic
    prefs_dir = scenario.preferences_dir or preferences_dir
    preferences_text = read_preferences(prefs_dir)
    prompt = engine.build_prompt(scenario.event, preferences_text, tools)

    # Call Ollama and measure latency
    start = time.perf_counter()
    response = await ollama_client.infer(prompt, model=model)
    latency_ms = (time.perf_counter() - start) * 1000

    # Parse using the engine's real logic
    valid_services = ToolRegistry.get_registered_services(tools)
    parsed_action = engine.parse_response(response, scenario.event, valid_services)

    return TraceRecord(
        trace_id=str(uuid4()),
        timestamp=datetime.now(UTC),
        model=model,
        event=scenario.event,
        preferences_text=preferences_text,
        tools=[asdict(t) for t in tools],
        prompt=prompt,
        raw_response=str(response.get("response", "")),
        parsed_action=parsed_action,
        latency_ms=latency_ms,
        prompt_tokens=int(response.get("prompt_tokens", 0)),
        completion_tokens=int(response.get("completion_tokens", 0)),
    )
