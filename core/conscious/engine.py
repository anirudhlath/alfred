"""Conscious Engine — LLM-powered System 2 reasoning with agentic tool-use loop.

Routes through OpenRouter via LiteLLM for provider-agnostic model access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

import litellm

from bus.schemas.events import ActionRequest, AlfredResponse, UserRequest
from core.conscious.action_tools import (
    ACTION_TOOL_NAMES,
    ACTION_TOOLS_MANIFEST,
    dispatch_action_tool,
)
from core.conscious.memory_tools import (
    MEMORY_TOOL_PREFIX,
    MEMORY_TOOLS_MANIFEST,
    dispatch_memory_tool,
)
from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry
from core.triggers.feature import TriggerFeature  # noqa: TC001 (runtime use)
from sdk.alfred_sdk.telemetry import track_latency
from shared.streams import SCRATCHPAD_QUEUE
from shared.traced import traced
from shared.type_map import PYTHON_TO_JSON_SCHEMA
from shared.usertime import (
    get_user_timezone,
    is_valid_timezone,
    set_user_timezone,
)

_debug = os.getenv("ALFRED_DEBUG", "").lower() in ("1", "true", "yes")
# LiteLLM logging: use LITELLM_LOG env var (official API).
# Set to ERROR by default to suppress verbose debug spam; ALFRED_DEBUG overrides to DEBUG.
if not os.getenv("LITELLM_LOG"):
    os.environ["LITELLM_LOG"] = "DEBUG" if _debug else "ERROR"
litellm.suppress_debug_info = not _debug
litellm.set_verbose = _debug  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from core.conscious.context_assembler import ContextAssembler
    from core.conscious.cost import CostTracker
    from core.conscious.identity import IdentityGate
    from core.conscious.session import SessionManager
    from core.memory.context_index import ContextIndexManager
    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.routines.store import RoutineStore
    from core.memory.schemas import RoutineSpec
    from core.memory.vector_store import SearchResult
    from core.reflex.context_reader import ContextReader
    from core.reflex.tool_registry import ToolInfo, ToolRegistry
    from core.routing.domain_router import DomainRouter
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


@dataclass
class ConsciousConfig:
    """LLM configuration for the Conscious Engine.

    Defaults are intentionally empty — populate from AlfredConfig at the call site.
    """

    model: str
    api_key: str = ""
    max_tokens: int = 2048
    involuntary_recall_limit: int = 10
    involuntary_recall_threshold: float = 0.4


@dataclass
class ConsciousDeps:
    """Service dependencies injected into the Conscious Engine."""

    redis: AioRedis
    identity_gate: IdentityGate
    session_mgr: SessionManager
    cost_tracker: CostTracker
    context_assembler: ContextAssembler
    domain_router: DomainRouter
    tool_registry: ToolRegistry
    context_reader: ContextReader
    routine_store: RoutineStore | None = None
    trigger_feature: TriggerFeature | None = None
    embedder: EmbeddingProvider | None = None
    context_index: ContextIndexManager | None = None
    config: ConsciousConfig = field(default_factory=lambda: ConsciousConfig(model=""))


class ConsciousEngine:
    """The conversational brain of Alfred (System 2).

    Receives UserRequest events, resolves identity, assembles context,
    runs a multi-step agentic tool-use loop, and returns AlfredResponse.

    Uses LiteLLM to route requests through OpenRouter or any supported provider.
    """

    def __init__(
        self,
        *,
        deps: ConsciousDeps | None = None,
        # Legacy kwargs — kept for backward compatibility with tests and call sites.
        redis: AioRedis | None = None,
        identity_gate: IdentityGate | None = None,
        session_mgr: SessionManager | None = None,
        cost_tracker: CostTracker | None = None,
        context_assembler: ContextAssembler | None = None,
        domain_router: DomainRouter | None = None,
        tool_registry: ToolRegistry | None = None,
        context_reader: ContextReader | None = None,
        claude_model: str = "openrouter/anthropic/claude-sonnet-4",
        claude_api_key: str = "",
        claude_max_tokens: int = 2048,
        routine_store: RoutineStore | None = None,
        trigger_feature: TriggerFeature | None = None,
        embedder: EmbeddingProvider | None = None,
        context_index: ContextIndexManager | None = None,
    ) -> None:
        if deps is not None:
            d = deps
            cfg = d.config
        else:
            # Build from individual kwargs (backward compat)
            required = {
                "redis": redis,
                "identity_gate": identity_gate,
                "session_mgr": session_mgr,
                "cost_tracker": cost_tracker,
                "context_assembler": context_assembler,
                "domain_router": domain_router,
                "tool_registry": tool_registry,
                "context_reader": context_reader,
            }
            missing = [k for k, v in required.items() if v is None]
            if missing:
                raise ValueError(f"Missing required deps: {', '.join(missing)}")
            # All required deps verified non-None above; narrow for mypy
            assert redis is not None
            assert identity_gate is not None
            assert session_mgr is not None
            assert cost_tracker is not None
            assert context_assembler is not None
            assert domain_router is not None
            assert tool_registry is not None
            assert context_reader is not None
            d = ConsciousDeps(
                redis=redis,
                identity_gate=identity_gate,
                session_mgr=session_mgr,
                cost_tracker=cost_tracker,
                context_assembler=context_assembler,
                domain_router=domain_router,
                tool_registry=tool_registry,
                context_reader=context_reader,
                routine_store=routine_store,
                trigger_feature=trigger_feature,
                embedder=embedder,
                context_index=context_index,
                config=ConsciousConfig(
                    model=claude_model,
                    api_key=claude_api_key,
                    max_tokens=claude_max_tokens,
                ),
            )
            cfg = d.config

        self._redis = d.redis
        self._identity_gate = d.identity_gate
        self._session_mgr = d.session_mgr
        self._cost = d.cost_tracker
        self._assembler = d.context_assembler
        self._router = d.domain_router
        self._tool_registry = d.tool_registry
        self._context_reader = d.context_reader
        self._model = cfg.model
        self._api_key = cfg.api_key
        self._max_tokens = cfg.max_tokens
        self._involuntary_recall_limit = cfg.involuntary_recall_limit
        self._involuntary_recall_threshold = cfg.involuntary_recall_threshold
        self._routines = d.routine_store
        self._triggers = d.trigger_feature
        self._embedder = d.embedder
        self._context_index = d.context_index

    @property
    def has_routine_store(self) -> bool:
        """Whether a routine store is configured."""
        return self._routines is not None

    _TYPE_MAP: ClassVar[dict[str, str]] = PYTHON_TO_JSON_SCHEMA

    @staticmethod
    def _sanitize_tool_name(name: str) -> str:
        """Convert dotted tool names to OpenAI-compatible format (a-zA-Z0-9_-)."""
        return name.replace(".", "_")

    @classmethod
    def _unsanitize_tool_name(cls, name: str, tools: list[ToolInfo]) -> str:
        """Reverse sanitized name back to original dotted form.

        Integration tool names (prefixed with 'integration_') are returned as-is.
        """
        if name.startswith(cls._INTEGRATION_PREFIX):
            return name
        sanitized_to_original = {t.name.replace(".", "_"): t.name for t in tools}
        return sanitized_to_original.get(name, name)

    def _to_json_schema_type(self, py_type: str) -> str:
        """Convert a Python type annotation string to a JSON Schema type."""
        # Strip Optional/None union syntax
        base = py_type.split("|")[0].strip().split("[")[0].strip()
        return self._TYPE_MAP.get(base, "string")

    def _tools_to_openai_format(self, tools: list[ToolInfo]) -> list[dict[str, Any]]:
        """Convert ToolInfo list to OpenAI function-calling format (used by LiteLLM)."""
        openai_tools: list[dict[str, Any]] = []
        for t in tools:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for pname, pinfo in t.parameters.items():
                properties[pname] = {
                    "type": self._to_json_schema_type(pinfo.get("type", "string")),
                    "description": pinfo.get("description", ""),
                }
                if "default" not in pinfo:
                    required.append(pname)

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": self._sanitize_tool_name(t.name),
                        "description": t.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return openai_tools

    # Prefix for integration tool names to distinguish from domain tools
    _INTEGRATION_PREFIX: ClassVar[str] = "integration_"

    async def _integrations_to_openai_format(self) -> list[dict[str, Any]]:
        """Convert integration capabilities to OpenAI function-calling format."""
        openai_tools: list[dict[str, Any]] = []
        for name in IntegrationRegistry.available():
            instance = IntegrationRegistry.get(name)
            try:
                caps = await instance.get_capabilities()
            except Exception:
                logger.warning("Failed to get capabilities for integration %s", name)
                continue
            for cap in caps:
                # Build tool name: integration_weather_get_current
                tool_name = f"{self._INTEGRATION_PREFIX}{name}_{cap.name}"
                properties = cap.params_schema.get("properties", {})
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": f"[{name}] {cap.description}",
                            "parameters": {
                                "type": "object",
                                "properties": properties,
                                "required": list(
                                    k
                                    for k, v in properties.items()
                                    if isinstance(v, dict) and "default" not in v
                                ),
                            },
                        },
                    }
                )
        return openai_tools

    async def _execute_integration_call(self, tool_name: str, params: dict[str, Any]) -> str:
        """Execute an integration tool call. Returns result as string."""
        # Parse: integration_weather_get_current -> integration="weather", action="get_current"
        stripped = tool_name.removeprefix(self._INTEGRATION_PREFIX)
        # Find which integration name matches (greedy match on registered names)
        integration_name = ""
        action = ""
        for name in IntegrationRegistry.available():
            prefix = f"{name}_"
            if stripped.startswith(prefix):
                integration_name = name
                action = stripped.removeprefix(prefix)
                break
        if not integration_name:
            return f"Error: unknown integration tool '{tool_name}'"

        instance = IntegrationRegistry.get(integration_name)
        request = IntegrationRequest(action=action, params=params)
        result = await instance.execute(request)
        if "error" in result.data:
            return f"Error: {result.data['error']}"
        return json.dumps(result.data)

    async def _call_llm(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        original_tools: list[ToolInfo] | None = None,
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        """Call LLM via LiteLLM. Returns (text, tool_calls, prompt_tokens, completion_tokens).

        Tool names are unsanitized back to dotted form
        using the original_tools list for reverse mapping.
        """
        if not self._api_key:
            return (
                "I'm afraid my connection to the thinking engine is not configured, sir.",
                [],
                0,
                0,
            )

        llm_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": llm_messages,
            "max_tokens": self._max_tokens,
            "api_key": self._api_key,
        }
        if tools:
            kwargs["tools"] = tools

        response = await litellm.acompletion(**kwargs)

        choice = response.choices[0]
        msg = choice.message

        text = msg.content or ""
        tool_calls: list[dict[str, Any]] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_input = tc.function.arguments
                if isinstance(tool_input, str):
                    try:
                        tool_input = json.loads(tool_input)
                    except json.JSONDecodeError:
                        tool_input = {}
                # Map sanitized name back to original dotted name
                fn_name = tc.function.name
                if original_tools:
                    fn_name = self._unsanitize_tool_name(fn_name, original_tools)
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": fn_name,
                        "input": tool_input,
                    }
                )

        usage = response.usage
        return (
            text,
            tool_calls,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )

    def _resolve_trigger_method(self, tool_name: str) -> str | None:
        """Check if tool_name is a trigger tool; return the method name if so."""
        if self._triggers is None:
            return None
        for t in self._triggers.get_tools():
            if t.name == tool_name:
                return tool_name.removeprefix(f"{self._triggers.feature_name}.")
        return None

    async def _execute_trigger_call(self, method: str, params: dict[str, Any]) -> str:
        """Execute a trigger tool call directly via TriggerFeature."""
        if self._triggers is None:
            return "Error: trigger feature not configured"
        fn = getattr(self._triggers, method)
        result: Any = await fn(**params)
        return json.dumps(result) if not isinstance(result, str) else result

    @staticmethod
    def _make_tool_result(call_id: str, content: str) -> dict[str, Any]:
        """Build an OpenAI-format tool result dict."""
        return {"type": "tool_result", "tool_use_id": call_id, "content": content}

    async def _dispatch_tool_call(
        self, tc: dict[str, Any], tools: list[ToolInfo]
    ) -> dict[str, Any]:
        """Dispatch a single tool call and return the result dict."""
        name = tc["name"]
        params = tc.get("input", {})

        # 1. Integration tools — direct call via IntegrationRegistry
        if name.startswith(self._INTEGRATION_PREFIX):
            try:
                content = await self._execute_integration_call(name, params)
            except Exception as e:
                content = f"Error executing integration: {e}"
            return self._make_tool_result(tc["id"], content)

        # 2. Trigger tools — direct in-process call (system-level feature)
        trigger_method = self._resolve_trigger_method(name)
        if trigger_method:
            try:
                content = await self._execute_trigger_call(trigger_method, params)
            except Exception as e:
                content = f"Error executing trigger: {e}"
            return self._make_tool_result(tc["id"], content)

        # 3. Memory tools — direct in-process call
        if name.startswith(MEMORY_TOOL_PREFIX):
            if self._context_index:
                try:
                    content = await dispatch_memory_tool(
                        name,
                        params,
                        self._context_index,
                        self._context_reader,
                    )
                except Exception as e:
                    content = f"Error executing memory tool: {e}"
            else:
                content = "Memory tools not available"
            return self._make_tool_result(tc["id"], content)

        # 3b. Action tools (confirmation + attention) — direct in-process call
        if name in ACTION_TOOL_NAMES:
            try:
                content = await dispatch_action_tool(name, params, self._redis)
            except Exception as e:
                content = f"Error executing action tool: {e}"
            return self._make_tool_result(tc["id"], content)

        # 4. Domain tools — route to external service via DomainRouter
        target = ""
        for t in tools:
            if t.name == name:
                target = t.target_service
                break

        if not target:
            return self._make_tool_result(tc["id"], f"Error: tool '{name}' not found in registry")

        action = ActionRequest(
            source="conscious-engine",
            target_service=target,
            tool_name=name,
            parameters=params,
        )
        action_result = await self._router.route(action)
        content = str(
            action_result.result if action_result.status == "success" else action_result.error
        )
        return self._make_tool_result(tc["id"], content)

    async def _execute_tool_calls(
        self, tool_calls: list[dict[str, Any]], tools: list[ToolInfo]
    ) -> list[dict[str, Any]]:
        """Execute tool calls concurrently via integration, trigger, or domain dispatch."""
        return list(
            await asyncio.gather(*(self._dispatch_tool_call(tc, tools) for tc in tool_calls))
        )

    _ROUTINE_SUGGESTION_COOLDOWN_HOURS: ClassVar[int] = 24

    def _eligible_candidates(self, now: datetime, tz_name: str) -> list[RoutineSpec]:
        """Return candidate routines that match current time and are outside cooldown."""
        if self._routines is None:
            return []

        from core.memory.routines.patterns import match_trigger_pattern

        eligible: list[RoutineSpec] = []
        for routine in self._routines.list_by_state("candidate"):
            if routine.last_suggested is not None:
                hours_since = (now - routine.last_suggested).total_seconds() / 3600
                if hours_since < self._ROUTINE_SUGGESTION_COOLDOWN_HOURS:
                    continue
            if not match_trigger_pattern(routine.trigger_pattern, now, tz_name):
                continue
            eligible.append(routine)
        return eligible

    def _build_routine_hint(self, now: datetime, tz_name: str) -> str:
        """Check candidate routines for time-pattern matches and return a hint string.

        Returns an empty string when no routines match or ``_routines`` is None.
        Routines that were suggested within the cooldown window are skipped.
        Matching routines have their ``last_suggested`` timestamp updated.
        """
        if self._routines is None:
            return ""

        eligible = self._eligible_candidates(now, tz_name)
        hints: list[str] = []

        for routine in eligible:
            updated = routine.model_copy(update={"last_suggested": now})
            self._routines.save(updated)

            steps_str = "; ".join(s.description for s in routine.steps) if routine.steps else "N/A"
            hints.append(
                f"[routine-suggestion] You've noticed a pattern: {routine.name} "
                f"({routine.trigger_pattern}). Steps: {steps_str}. "
                f"Confidence: {routine.confidence:.0%}. "
                f"If appropriate, suggest this to sir and ask if they'd like "
                f"Alfred to handle this automatically."
            )
            logger.debug("Routine suggestion injected: '%s'", routine.name)

        return "\n\n".join(hints)

    async def check_routine_suggestions(
        self,
        now: datetime | None = None,
        notifier: Any = None,
    ) -> None:
        """Check candidate routines and publish proactive notifications for matches.

        Called periodically from the conscious process background loop.
        Routines that match the current time pattern and are outside the suggestion
        cooldown window receive an INFORMATIONAL notification push.
        """
        if self._routines is None or notifier is None:
            return

        from core.notifications.schema import Urgency

        now = now or datetime.now(UTC)
        tz_name = await get_user_timezone(self._redis)

        for routine in self._eligible_candidates(now, tz_name):
            steps_str = "; ".join(s.description for s in routine.steps) if routine.steps else ""
            if steps_str:
                body = (
                    f"I've noticed a pattern: '{routine.name}' — {steps_str} "
                    f"around {routine.trigger_pattern}. "
                    f"Want me to start doing this automatically?"
                )
            else:
                body = (
                    f"I've noticed a recurring pattern: '{routine.name}' "
                    f"around {routine.trigger_pattern}. "
                    f"Want me to start doing this automatically?"
                )

            # Update last_suggested BEFORE publishing to avoid spam if publish raises
            updated = routine.model_copy(update={"last_suggested": now})
            self._routines.save(updated)

            await notifier.publish(
                title="Routine Suggestion",
                body=body,
                source="librarian",
                urgency=Urgency.INFORMATIONAL,
            )
            logger.info("Proactive routine suggestion published: '%s'", routine.name)

    @track_latency(category="conscious")
    @traced(name="conscious.process_request")
    async def process_request(self, request: UserRequest) -> AlfredResponse:
        """Process a user request through the full pipeline."""
        now = datetime.now(UTC)
        if request.timezone and is_valid_timezone(request.timezone):
            tz_name = request.timezone
            # Persist the client-supplied timezone at the domain boundary, before
            # any tool dispatch (run_at normalization / cron) reads the stored key.
            # set_user_timezone is write-on-change — one cheap GET per request.
            await set_user_timezone(self._redis, request.timezone)
        else:
            tz_name = await get_user_timezone(self._redis)

        # 1. Identity Gate
        identity = self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.authenticated,
            identity_confidence=request.identity_confidence,
        )
        logger.info(
            "Identity resolved: %s (method=%s, confidence=%.2f)",
            identity.identity,
            identity.method,
            identity.confidence,
        )

        # 2. Budget check
        if await self._cost.is_budget_exceeded():
            logger.warning("Daily budget exceeded — returning System 1 fallback")
            return AlfredResponse(
                source="conscious-engine",
                channel=request.channel,
                session_id=request.session_id,
                text=(
                    "I'm afraid we've reached the daily budget, sir. "
                    "I'm operating in reduced capacity — ambient actions continue, "
                    "but I'll need to defer complex requests until tomorrow."
                ),
                actions_taken=[],
                mood="concerned",
            )

        # 3. Session
        session = await self._session_mgr.get_or_create(request.session_id, request.channel)

        # 3b. Involuntary recall — embed user query, search unified context index
        involuntary_context: list[SearchResult] = []
        if self._context_index and request.content:
            try:
                involuntary_context = await self._context_index.search_text(
                    request.content,
                    limit=self._involuntary_recall_limit,
                    min_similarity=self._involuntary_recall_threshold,
                )
            except Exception:
                logger.warning("Involuntary recall failed", exc_info=True)

        # 3c. Routine suggestion — check candidate routines against current time
        routine_hint: str = ""
        if self.has_routine_store:
            try:
                routine_hint = self._build_routine_hint(now, tz_name)
            except Exception:
                logger.warning("Routine suggestion check failed", exc_info=True)

        # 4. Context assembly
        tools = await self._tool_registry.get_tools()

        # Integrations are exposed as callable tools (not prompt text).
        # Pass a truthy flag so the assembler emits the integration hint for sir.
        has_integrations = bool(IntegrationRegistry.available())

        system_prompt = self._assembler.assemble(
            identity=identity,
            tools_section="\n".join(f"- {t.name}: {t.description}" for t in tools),
            integrations_section="available" if has_integrations else "",
            proactivity_level="opinionated",
            now=now,
            relevant_context=involuntary_context if involuntary_context else None,
            channel=request.channel,
            content_type=request.content_type,
            area=request.area,
            tz_name=tz_name,
        )
        if routine_hint:
            system_prompt = system_prompt + "\n\n" + routine_hint

        # 5. Build messages
        messages: list[dict[str, Any]] = list(session["history"])
        messages.append({"role": "user", "content": request.content})

        # 6. Agentic loop
        openai_tools = self._tools_to_openai_format(tools)
        # Add integration capabilities as callable tools (sir only)
        if identity.identity == "sir":
            try:
                integration_tools = await self._integrations_to_openai_format()
                openai_tools.extend(integration_tools)
            except Exception:
                logger.warning("Failed to build integration tools", exc_info=True)
        # Add memory tools (sir only)
        if identity.identity == "sir" and self._context_index:
            openai_tools.extend(MEMORY_TOOLS_MANIFEST)
        # Add confirmation + attention tools (sir only)
        if identity.identity == "sir":
            openai_tools.extend(ACTION_TOOLS_MANIFEST)
        total_prompt_tokens = 0
        total_completion_tokens = 0
        all_actions: list[str] = []
        final_text = ""

        for _iteration in range(MAX_ITERATIONS):
            text, tool_calls, pt, ct = await self._call_llm(
                system_prompt, messages, openai_tools, original_tools=tools
            )
            total_prompt_tokens += pt
            total_completion_tokens += ct

            if not tool_calls:
                # Final response — no more tool calls
                final_text = text
                break

            # Execute tools and feed results back (uses original dotted names)
            tool_results = await self._execute_tool_calls(tool_calls, tools=tools)
            all_actions.extend(tc["name"] for tc in tool_calls)

            # Append assistant turn with tool calls (OpenAI format — sanitized names)
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if text:
                assistant_msg["content"] = text
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": self._sanitize_tool_name(tc["name"]),
                        "arguments": (
                            json.dumps(tc["input"])
                            if isinstance(tc["input"], dict)
                            else str(tc["input"])
                        ),
                    },
                }
                for tc in tool_calls
            ]
            messages.append(assistant_msg)

            # Append tool results (OpenAI format)
            for tr, tc in zip(tool_results, tool_calls, strict=True):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tr["content"],
                    }
                )
        else:
            if not final_text:
                final_text = (
                    "I apologize, sir — I've been deliberating too long. "
                    "Let me try a more direct approach."
                )

        # 7. Record cost
        await self._cost.record_spend(total_prompt_tokens, total_completion_tokens, self._model)

        # 8. Update session
        await self._session_mgr.append_turn(request.session_id, "user", request.content)
        await self._session_mgr.append_turn(request.session_id, "assistant", final_text)

        # 8b. Write observation to scratchpad queue
        try:
            timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            actions_str = ", ".join(all_actions) if all_actions else "none"
            observation = (
                f"{timestamp} [conscious] "
                f"user='{request.content[:80]}' → {len(final_text)} chars "
                f"(actions={actions_str}, tokens={total_prompt_tokens}+{total_completion_tokens})"
            )
            await self._redis.lpush(SCRATCHPAD_QUEUE, observation)
        except Exception as exc:
            logger.warning("Failed to write scratchpad observation: %s", exc)

        # 9. Build response
        return AlfredResponse(
            source="conscious-engine",
            channel=request.channel,
            session_id=request.session_id,
            text=final_text,
            actions_taken=all_actions,
            mood="neutral",
        )
