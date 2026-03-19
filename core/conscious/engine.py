"""Conscious Engine — Claude-powered System 2 reasoning with agentic tool-use loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anthropic

from bus.schemas.events import ActionRequest, AlfredResponse, UserRequest
from sdk.alfred_sdk.telemetry import track_latency
from shared.traced import traced

if TYPE_CHECKING:
    from core.conscious.context_assembler import ContextAssembler
    from core.conscious.cost import CostTracker
    from core.conscious.identity import IdentityGate
    from core.conscious.session import SessionManager
    from core.reflex.context_reader import ContextReader
    from core.reflex.runner import AioRedis
    from core.reflex.tool_registry import ToolInfo, ToolRegistry
    from core.routing.domain_router import DomainRouter

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


class ConsciousEngine:
    """The conversational brain of Alfred (System 2).

    Receives UserRequest events, resolves identity, assembles context,
    runs a multi-step Claude agentic loop, and returns AlfredResponse.
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
        claude_model: str = "claude-opus-4-6",
        claude_api_key: str = "",
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
        self._client = anthropic.AsyncAnthropic(api_key=claude_api_key) if claude_api_key else None

    def _tools_to_claude_format(self, tools: list[ToolInfo]) -> list[dict[str, Any]]:
        """Convert ToolInfo list to Anthropic tool-use format."""
        claude_tools: list[dict[str, Any]] = []
        for t in tools:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for pname, pinfo in t.parameters.items():
                properties[pname] = {
                    "type": pinfo.get("type", "string"),
                    "description": pinfo.get("description", ""),
                }
                if "default" not in pinfo:
                    required.append(pname)

            claude_tools.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }
            )
        return claude_tools

    async def _call_claude(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        """Call Claude API. Returns (text, tool_calls, prompt_tokens, completion_tokens)."""
        if self._client is None:
            return (
                "I'm afraid my connection to the thinking engine is not configured, sir.",
                [],
                0,
                0,
            )

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        return (
            "\n".join(text_parts),
            tool_calls,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    async def _execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Execute tool calls via DomainRouter and return results."""
        results: list[dict[str, Any]] = []
        for tc in tool_calls:
            # Tool name format: feature.method -> target_service looked up from registry
            tools = await self._tool_registry.get_tools()
            target = ""
            for t in tools:
                if t.name == tc["name"]:
                    target = t.target_service
                    break

            if not target:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": f"Error: tool '{tc['name']}' not found in registry",
                    }
                )
                continue

            action = ActionRequest(
                source="conscious-engine",
                target_service=target,
                tool_name=tc["name"],
                parameters=tc.get("input", {}),
            )
            action_result = await self._router.route(action)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": str(
                        action_result.result
                        if action_result.status == "success"
                        else action_result.error
                    ),
                }
            )
        return results

    @track_latency(category="conscious")
    @traced(name="conscious.process_request")
    async def process_request(self, request: UserRequest) -> AlfredResponse:
        """Process a user request through the full pipeline."""
        # 1. Identity Gate
        identity = self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.identity_claim == "sir",  # Simplified for now
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
        preferences = ""  # TODO: Read from memory (Plan 3)

        system_prompt = self._assembler.assemble(
            identity=identity,
            tools_section="\n".join(f"- {t.name}: {t.description}" for t in tools),
            integrations_section="",  # TODO: IntegrationRegistry (Plan 4)
            preferences_text=preferences,
            context_text=context_text,
            history=session["history"],
        )

        # 5. Build messages
        messages: list[dict[str, Any]] = list(session["history"])
        messages.append({"role": "user", "content": request.content})

        # 6. Agentic loop
        claude_tools = self._tools_to_claude_format(tools)
        total_prompt_tokens = 0
        total_completion_tokens = 0
        all_actions: list[str] = []
        final_text = ""

        for _iteration in range(MAX_ITERATIONS):
            text, tool_calls, pt, ct = await self._call_claude(
                system_prompt, messages, claude_tools
            )
            total_prompt_tokens += pt
            total_completion_tokens += ct

            if not tool_calls:
                # Final response — no more tool calls
                final_text = text
                break

            # Execute tools and feed results back
            tool_results = await self._execute_tool_calls(tool_calls)
            all_actions.extend(tc["name"] for tc in tool_calls)

            # Append assistant turn with tool use
            content_blocks: list[dict[str, Any]] = []
            if text:
                content_blocks.append({"type": "text", "text": text})
            content_blocks.extend(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                }
                for tc in tool_calls
            )
            messages.append({"role": "assistant", "content": content_blocks})
            # Append tool results
            messages.append({"role": "user", "content": tool_results})
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

        # 9. Build response
        return AlfredResponse(
            source="conscious-engine",
            channel=request.channel,
            session_id=request.session_id,
            text=final_text,
            actions_taken=all_actions,
            mood="neutral",
        )
