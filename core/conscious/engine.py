"""Conscious Engine — LLM-powered System 2 reasoning with agentic tool-use loop.

Routes through OpenRouter via LiteLLM for provider-agnostic model access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import litellm

from bus.schemas.events import ActionRequest, AlfredResponse, UserRequest
from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry
from core.triggers.feature import TriggerFeature  # noqa: TC001 (runtime use)
from sdk.alfred_sdk.telemetry import track_latency
from shared.streams import SCRATCHPAD_QUEUE
from shared.traced import traced
from shared.type_map import PYTHON_TO_JSON_SCHEMA

_debug = os.getenv("ALFRED_DEBUG", "").lower() in ("1", "true", "yes")
litellm.suppress_debug_info = not _debug
litellm.set_verbose = _debug  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from core.conscious.context_assembler import ContextAssembler
    from core.conscious.cost import CostTracker
    from core.conscious.identity import IdentityGate
    from core.conscious.memory_reader import MemoryReader
    from core.conscious.session import SessionManager
    from core.memory.episodic.store import EpisodicStore
    from core.memory.routines.store import RoutineStore
    from core.reflex.context_reader import ContextReader
    from core.reflex.runner import AioRedis
    from core.reflex.tool_registry import ToolInfo, ToolRegistry
    from core.routing.domain_router import DomainRouter

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


class ConsciousEngine:
    """The conversational brain of Alfred (System 2).

    Receives UserRequest events, resolves identity, assembles context,
    runs a multi-step agentic tool-use loop, and returns AlfredResponse.

    Uses LiteLLM to route requests through OpenRouter or any supported provider.
    """

    def __init__(
        self,
        redis: AioRedis,
        identity_gate: IdentityGate,
        session_mgr: SessionManager,
        cost_tracker: CostTracker,
        context_assembler: ContextAssembler,
        domain_router: DomainRouter,
        tool_registry: ToolRegistry,
        context_reader: ContextReader,
        claude_model: str = "openrouter/anthropic/claude-sonnet-4",
        claude_api_key: str = "",
        memory_reader: MemoryReader | None = None,
        episodic_store: EpisodicStore | None = None,
        routine_store: RoutineStore | None = None,
        trigger_feature: TriggerFeature | None = None,
    ) -> None:
        self._redis = redis
        self._identity_gate = identity_gate
        self._session_mgr = session_mgr
        self._cost = cost_tracker
        self._assembler = context_assembler
        self._router = domain_router
        self._tool_registry = tool_registry
        self._context_reader = context_reader
        self._model = claude_model
        self._api_key = claude_api_key
        self._memory_reader = memory_reader
        self._episodic = episodic_store
        self._routines = routine_store
        self._triggers = trigger_feature

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
            "max_tokens": 2048,
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

        # 3. Domain tools — route to external service via DomainRouter
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

    @track_latency(category="conscious")
    @traced(name="conscious.process_request")
    async def process_request(self, request: UserRequest) -> AlfredResponse:
        """Process a user request through the full pipeline."""
        now = datetime.now(UTC)

        # 1. Identity Gate
        identity = self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.authenticated,
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

        # 4. Context assembly
        tools = await self._tool_registry.get_tools()
        context_text = await self._context_reader.get_rendered_context()

        # 4a. Read semantic memory (preferences + profile)
        preferences = ""
        proactivity_level = "opinionated"
        if self._memory_reader:
            try:
                prefs = self._memory_reader.get_preferences()
                profile = self._memory_reader.get_profile()
                preferences = "\n\n".join(p for p in (prefs, profile) if p)
                proactivity_level = self._memory_reader.get_proactivity_level()
            except Exception:
                logger.warning("Failed to read semantic memory", exc_info=True)

        # 4b. Read episodic memory (recent entries from cold storage)
        episodic_text = ""
        if self._episodic:
            try:
                since = now - timedelta(days=7)
                entries = await self._episodic.query_cold(limit=10, since=since)
                if entries:
                    lines = [f"- [{e.timestamp:%Y-%m-%d}] {e.summary}" for e in entries]
                    episodic_text = "\n".join(lines)
            except Exception:
                logger.warning("Failed to read episodic memory", exc_info=True)

        # 4c. Read procedural memory (active routines)
        procedural_text = ""
        if self._routines:
            try:
                active = self._routines.list_by_state("active")
                if active:
                    lines = [f"- {r.name}: {r.trigger_pattern}" for r in active]
                    procedural_text = "\n".join(lines)
            except Exception:
                logger.warning("Failed to read procedural memory", exc_info=True)

        # 4d. Integrations are now exposed as callable tools (not prompt text).
        # Pass a truthy flag so the assembler emits the integration hint for sir.
        has_integrations = bool(IntegrationRegistry.available())

        system_prompt = self._assembler.assemble(
            identity=identity,
            tools_section="\n".join(f"- {t.name}: {t.description}" for t in tools),
            integrations_section="available" if has_integrations else "",
            now=now,
            preferences_text=preferences,
            context_text=context_text,
            history=session["history"],
            proactivity_level=proactivity_level,
            episodic_text=episodic_text,
            procedural_text=procedural_text,
            channel=request.channel,
            content_type=request.content_type,
        )

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
