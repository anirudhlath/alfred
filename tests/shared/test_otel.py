"""Tests for OpenTelemetry SDK initialization."""

from __future__ import annotations

from shared.otel import init_tracing


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
