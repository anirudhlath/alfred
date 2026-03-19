"""OpenTelemetry SDK initialization for Alfred services."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


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
