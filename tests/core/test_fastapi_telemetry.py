"""FastAPI's own telemetry must not add a second span exporter (#333).

FastAPI 0.142+ adds an OTLP exporter of its own at app startup whenever
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. It only speaks OTLP/HTTP, and that variable names
``init_tracing``'s gRPC endpoint, so the extra exporter posted every batch to the gRPC port
and the channels process logged a "Connection reset by peer" export error about every 5 s.

Each probe runs in a fresh interpreter: the tracer provider is process-global, and FastAPI
keeps its own exporter registrations in module state. It builds the real pipeline through
``init_tracing`` (its gRPC exporter swapped for an in-memory one), starts the app, makes one
request, and reports which OTLP/HTTP exporters FastAPI asked for and which spans reached
``init_tracing``'s exporter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_PROBE = textwrap.dedent(
    """
    import json
    import sys
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc import trace_exporter as otlp_grpc
    from opentelemetry.exporter.otlp.proto.http import _log_exporter as otlp_http_logs
    from opentelemetry.exporter.otlp.proto.http import metric_exporter as otlp_http_metrics
    from opentelemetry.exporter.otlp.proto.http import trace_exporter as otlp_http_traces
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    otlp_grpc.OTLPSpanExporter = lambda **_kwargs: exporter

    # FastAPI imports these when it configures export, so the stubs see every request
    # for one. Each refuses after recording, so a regression never reaches the network.
    requested = []

    def refuse(signal):
        def build(**_kwargs):
            requested.append(signal)
            raise RuntimeError("FastAPI asked for an OTLP/HTTP " + signal + " exporter")
        return build

    otlp_http_traces.OTLPSpanExporter = refuse("traces")
    otlp_http_metrics.OTLPMetricExporter = refuse("metrics")
    otlp_http_logs.OTLPLogExporter = refuse("logs")

    from shared.otel import init_tracing

    init_tracing(service_name="probe", endpoint="http://localhost:14317")

    if sys.argv[1] == "channels":
        import core.channels.web_server as web_server

        # The real lifespan connects to Redis; FastAPI configures export around it.
        @asynccontextmanager
        async def no_lifespan(_app):
            yield

        web_server._lifespan = no_lifespan
        app = web_server.create_app()
    else:
        from core.triggers.server import create_app

        app = create_app(AsyncMock(), AsyncMock())

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    trace.get_tracer_provider().force_flush()
    print(json.dumps({
        "requested": requested,
        "spans": [span.name for span in exporter.get_finished_spans()],
    }))
    """
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("app", ["channels", "triggers"])
def test_fastapi_adds_no_exporter_and_its_spans_reach_init_tracing(
    app: str, tmp_path: Path
) -> None:
    env = {
        **os.environ,
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:14317",
        "ALFRED_DATA_DIR": str(tmp_path),
    }

    result = subprocess.run(
        [sys.executable, "-c", _PROBE, app],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["requested"] == []
    # Turning FastAPI's exporter off keeps its request spans: they reach the collector
    # through the provider init_tracing installed.
    assert any("/health" in name for name in report["spans"]), report["spans"]
