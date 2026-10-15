"""Tests for OpenTelemetry SDK initialization."""

from __future__ import annotations

from unittest.mock import patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared.otel import (
    LANGFUSE_SESSION_ID,
    LANGFUSE_TAGS,
    LANGFUSE_USER_ID,
    LangfuseBaggageProcessor,
    init_tracing,
    langfuse_attributes,
)


def test_init_tracing_creates_tracer() -> None:
    """init_tracing returns a working tracer."""
    tracer = init_tracing(service_name="test-service", endpoint=None)
    assert tracer is not None
    span = tracer.start_span("test-span")
    span.end()


def test_init_tracing_without_endpoint_uses_noop() -> None:
    """When endpoint is None, tracer still works (noop exporter)."""
    tracer = init_tracing(service_name="test-noop", endpoint=None)
    with tracer.start_as_current_span("noop-test") as span:
        assert span is not None


def test_langfuse_attributes_land_on_every_span_started_inside() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(LangfuseBaggageProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("t")

    with (
        langfuse_attributes(session_id="s-1", user_id="sir", tags=["web_pwa"]),
        tracer.start_as_current_span("outer"),
        tracer.start_as_current_span("inner"),
    ):
        pass
    with tracer.start_as_current_span("after"):
        pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    for name in ("outer", "inner"):
        attrs = spans[name].attributes or {}
        assert attrs[LANGFUSE_SESSION_ID] == "s-1"
        assert attrs[LANGFUSE_USER_ID] == "sir"
        assert attrs[LANGFUSE_TAGS] == ("web_pwa",)
    assert LANGFUSE_SESSION_ID not in (spans["after"].attributes or {})


def test_init_tracing_copies_langfuse_baggage_onto_spans() -> None:
    with (
        patch("shared.otel.turn_off_env_exporters"),
        patch("shared.otel.trace.set_tracer_provider") as set_provider,
    ):
        init_tracing("svc", endpoint=None)
    provider = set_provider.call_args.args[0]

    with langfuse_attributes(session_id="s-2", user_id="guest", tags=["signal"]):
        span = provider.get_tracer("t").start_span("x")
    span.end()

    assert (span.attributes or {})[LANGFUSE_USER_ID] == "guest"


def test_init_tracing_registers_the_prompt_link_processor() -> None:
    from shared.prompts.link import PROMPTS_ATTR, linked_prompts
    from shared.prompts.template import PromptRef

    with (
        patch("shared.otel.turn_off_env_exporters"),
        patch("shared.otel.trace.set_tracer_provider") as set_provider,
    ):
        init_tracing("svc", endpoint=None)
    provider = set_provider.call_args.args[0]

    with linked_prompts(PromptRef("librarian-analyse", None, "repo")):
        chat = provider.get_tracer("t").start_span(
            "chat m", attributes={"gen_ai.operation.name": "chat"}
        )
        other = provider.get_tracer("t").start_span("agent run")
    chat.end()
    other.end()

    assert (chat.attributes or {})[PROMPTS_ATTR] == ("librarian-analyse@repo",)
    assert PROMPTS_ATTR not in (other.attributes or {})
