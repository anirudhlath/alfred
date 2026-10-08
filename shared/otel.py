"""OpenTelemetry SDK initialization for Alfred services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

if TYPE_CHECKING:
    from fastapi.telemetry import TelemetryConfig

# Every FastAPI app passes this as ``FastAPI(telemetry=...)``. Since 0.142, FastAPI adds an
# OTLP exporter of its own at startup whenever OTEL_EXPORTER_OTLP_ENDPOINT is set. It only
# speaks OTLP/HTTP, and that variable names init_tracing's gRPC endpoint, so the second
# exporter posted every batch to the gRPC port and was reset (issue #333). Export belongs
# to init_tracing alone; FastAPI's request and WebSocket spans still reach the collector
# through the provider it installs.
FASTAPI_TELEMETRY: Final[TelemetryConfig] = {"auto_configure": False}


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
