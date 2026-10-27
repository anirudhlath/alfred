"""No FastAPI app adds a span exporter of its own (#333).

FastAPI 0.142+ adds OTLP exporters at app startup whenever ``OTEL_EXPORTER_OTLP_ENDPOINT``
is set. They only speak OTLP/HTTP, and that variable names ``init_tracing``'s gRPC
endpoint, so the extra exporter posted every batch to the gRPC port and the channels
process logged a "Connection reset by peer" export error about every 5 s.

``init_tracing`` turns environment-configured exporters off in its process's environment,
which every process the runner starts inherits. Each probe runs in a fresh interpreter: the
tracer provider is process-global, and FastAPI keeps its own exporter registrations in
module state. It builds the real pipeline through ``init_tracing`` (its gRPC exporter
swapped for an in-memory one), starts an app, makes one request, and reports which
OTLP/HTTP exporters FastAPI asked for and which spans reached ``init_tracing``'s exporter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared.otel import ENV_EXPORTERS_OFF, turn_off_env_exporters

# Stubs FastAPI's OTLP/HTTP exporters to record each request and refuse it, so a
# regression never reaches the network. FastAPI imports them when it configures export.
_REFUSE_HTTP_EXPORTERS = """
from opentelemetry.exporter.otlp.proto.http import _log_exporter as otlp_http_logs
from opentelemetry.exporter.otlp.proto.http import metric_exporter as otlp_http_metrics
from opentelemetry.exporter.otlp.proto.http import trace_exporter as otlp_http_traces

requested = []

def refuse(signal):
    def build(**_kwargs):
        requested.append(signal)
        raise RuntimeError("FastAPI asked for an OTLP/HTTP " + signal + " exporter")
    return build

otlp_http_traces.OTLPSpanExporter = refuse("traces")
otlp_http_metrics.OTLPMetricExporter = refuse("metrics")
otlp_http_logs.OTLPLogExporter = refuse("logs")
"""

# A FastAPI app no Alfred code constructs — home-service's, which the runner starts as a
# child process. It never calls init_tracing; it only inherits the runner's environment.
_FOREIGN_APP = textwrap.dedent(_REFUSE_HTTP_EXPORTERS) + textwrap.dedent(
    """
        import json

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/health")
        def health():
            return {"status": "ok"}

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

        print(json.dumps({"requested": requested, "spans": []}))
        """
)

_PROBE = (
    textwrap.dedent(
        """
        import json
        import subprocess
        import sys
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock

        from fastapi.testclient import TestClient
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc import trace_exporter as otlp_grpc
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        otlp_grpc.OTLPSpanExporter = lambda **_kwargs: exporter
        """
    )
    + textwrap.dedent(_REFUSE_HTTP_EXPORTERS)
    + textwrap.dedent(
        """
        from shared.otel import init_tracing

        init_tracing(service_name="probe", endpoint="http://localhost:14317")

        if sys.argv[1] == "child":
            # As the runner starts a service: a new process, this one's environment.
            child = subprocess.run(
                [sys.executable, "-c", sys.argv[2]], capture_output=True, text=True, check=True
            )
            print(child.stdout.strip().splitlines()[-1])
            raise SystemExit(0)

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
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _probe(args: list[str], tmp_path: Path) -> dict[str, list[str]]:
    env = {
        # Not inherited from this test process: the probe proves init_tracing sets them.
        **{k: v for k, v in os.environ.items() if k not in ENV_EXPORTERS_OFF},
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:14317",
        "ALFRED_DATA_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, *args],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report: dict[str, list[str]] = json.loads(result.stdout.strip().splitlines()[-1])
    return report


@pytest.mark.parametrize("app", ["channels", "triggers"])
def test_alfreds_apps_add_no_exporter_and_their_spans_reach_init_tracing(
    app: str, tmp_path: Path
) -> None:
    report = _probe([app], tmp_path)

    assert report["requested"] == []
    # FastAPI's request spans still reach the collector, through the provider
    # init_tracing installed.
    assert any("/health" in name for name in report["spans"]), report["spans"]


def test_an_app_in_a_process_init_tracing_started_adds_no_exporter(tmp_path: Path) -> None:
    report = _probe(["child", _FOREIGN_APP], tmp_path)

    assert report["requested"] == []


def test_an_exporter_set_explicitly_is_kept() -> None:
    env = {"OTEL_TRACES_EXPORTER": "console"}

    turn_off_env_exporters(env)

    assert env == {**ENV_EXPORTERS_OFF, "OTEL_TRACES_EXPORTER": "console"}
