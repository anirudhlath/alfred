"""Model-call spans carry the prompt versions the call used."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings

from shared.prompts.link import (
    PROMPT_NAME_ATTR,
    PROMPT_VERSION_ATTR,
    PROMPTS_ATTR,
    PromptLinkProcessor,
    linked_prompts,
)
from shared.prompts.template import PromptRef


def _hi(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("hi")])


def _traced_agent() -> tuple[Agent[None, str], InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(PromptLinkProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    agent: Agent[None, str] = Agent(
        FunctionModel(_hi),
        instructions="x",
        capabilities=[Instrumentation(settings=InstrumentationSettings(tracer_provider=provider))],
    )
    return agent, exporter


@pytest.mark.asyncio
async def test_chat_spans_carry_the_primary_prompt_and_every_version() -> None:
    agent, exporter = _traced_agent()
    primary = PromptRef("system2-personality", 4, "langfuse")
    other = PromptRef("system2-identity-sir", None, "repo")

    with linked_prompts(primary, other):
        await agent.run("q")

    spans = exporter.get_finished_spans()
    (chat,) = [s for s in spans if s.name.startswith("chat")]
    attrs = chat.attributes or {}
    assert attrs[PROMPT_NAME_ATTR] == "system2-personality"
    assert attrs[PROMPT_VERSION_ATTR] == 4
    assert attrs[PROMPTS_ATTR] == ("system2-personality@4", "system2-identity-sir@repo")
    for span in spans:
        if not span.name.startswith("chat"):
            assert PROMPT_NAME_ATTR not in (span.attributes or {})


@pytest.mark.asyncio
async def test_a_repo_default_is_listed_but_not_linked() -> None:
    agent, exporter = _traced_agent()
    with linked_prompts(PromptRef("librarian-analyse", None, "repo")):
        await agent.run("q")
    (chat,) = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")]
    attrs = chat.attributes or {}
    assert PROMPT_NAME_ATTR not in attrs
    assert attrs[PROMPTS_ATTR] == ("librarian-analyse@repo",)


@pytest.mark.asyncio
async def test_a_redis_copy_links_to_its_langfuse_version() -> None:
    agent, exporter = _traced_agent()
    with linked_prompts(PromptRef("librarian-analyse", 2, "redis")):
        await agent.run("q")
    (chat,) = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")]
    assert (chat.attributes or {})[PROMPT_VERSION_ATTR] == 2


@pytest.mark.asyncio
async def test_nothing_is_set_outside_a_link() -> None:
    agent, exporter = _traced_agent()
    await agent.run("q")
    for span in exporter.get_finished_spans():
        assert PROMPTS_ATTR not in (span.attributes or {})
