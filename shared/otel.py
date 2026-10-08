"""OpenTelemetry SDK initialization for Alfred services."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

if TYPE_CHECKING:
    from collections.abc import MutableMapping

# Export belongs to init_tracing alone. OpenTelemetry's standard variables also drive every
# component that adds exporters from the environment: since 0.142, FastAPI adds OTLP
# exporters at startup whenever OTEL_EXPORTER_OTLP_ENDPOINT is set. Those only speak
# OTLP/HTTP, and that variable names init_tracing's gRPC endpoint, so they posted every
# batch to the gRPC port and were reset (issue #333). "none" turns each of them off;
# init_tracing builds its own exporter and reads none of these. FastAPI's request and
# WebSocket spans still reach the collector through the provider init_tracing installs.
ENV_EXPORTERS_OFF: Final = {
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
}


def turn_off_env_exporters(env: MutableMapping[str, str]) -> None:
    """Default ``env`` to no environment-configured exporters, keeping any set explicitly."""
    for name, value in ENV_EXPORTERS_OFF.items():
        env.setdefault(name, value)


def init_tracing(
    service_name: str,
    endpoint: str | None = "http://localhost:4317",
) -> trace.Tracer:
    """Initialize OpenTelemetry tracing and return a Tracer.

    Args:
        service_name: Name of the service (appears in SigNoz).
        endpoint: OTLP gRPC endpoint. None = console exporter only (dev/test).

    Returns:
        An OpenTelemetry Tracer instance.
    """
    # In this process's own environment, so every process it starts inherits the default
    # too: the runner calls this before launching any service, home-service included,
    # whose FastAPI app Alfred never constructs.
    turn_off_env_exporters(os.environ)
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to initialize OTLP exporter at %s, falling back to console",
                endpoint,
                exc_info=True,
            )
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # Dev/test mode — no export
        pass

    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
