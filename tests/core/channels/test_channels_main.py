"""Channels entrypoint — uvicorn is told which proxy to trust for X-Forwarded-*."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import core.channels.__main__ as entry
from core.channels.__main__ import _DEFAULT_FORWARDED_ALLOW_IPS
from core.notifications.channels import ChannelRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _restore_channel_registry() -> Iterator[None]:
    """main() registers the websocket adapter on the process-global registry —
    snapshot/restore so this module never leaks into other test modules."""
    snapshot = dict(ChannelRegistry._instances)
    yield
    ChannelRegistry._instances.clear()
    ChannelRegistry._instances.update(snapshot)


@pytest.fixture(autouse=True)
def tracing_stub(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """main() installs the process-global tracer provider — stubbed so these tests
    never point it at a collector. The export test below runs the real one in a child
    interpreter instead."""
    stub = MagicMock()
    monkeypatch.setattr(entry, "init_tracing", stub)
    return stub


# --- the wiring: main() hands uvicorn what the resolver returned -------------------


def _run_main() -> MagicMock:
    """Run the entrypoint with the app, the server and logging setup stubbed out."""
    with (
        patch.object(entry, "create_app", return_value=MagicMock()),
        patch.object(entry, "configure_logging"),
        patch.object(entry.uvicorn, "run") as run,
    ):
        entry.main()
    return run


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, _DEFAULT_FORWARDED_ALLOW_IPS),
        # Blank is how .env.example ships keys and what compose injects for an unset
        # key. It must NOT reach uvicorn — a blank trusts nothing, so X-Forwarded-*
        # is never applied and the gate sees the proxy instead of the real client.
        ("", _DEFAULT_FORWARDED_ALLOW_IPS),
        ("   ", _DEFAULT_FORWARDED_ALLOW_IPS),
        ("172.18.0.5", "172.18.0.5"),
        ("172.18.0.5/32,127.0.0.1", "172.18.0.5/32,127.0.0.1"),
    ],
)
def test_main_passes_forwarded_allow_ips(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: str
) -> None:
    if env_value is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", env_value)
    monkeypatch.setenv("CHANNELS_PORT", "18081")

    run = _run_main()

    kwargs = run.call_args.kwargs
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == expected
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 18081


def test_main_uses_the_resolver() -> None:
    """main() forwards whatever the resolver returns, without re-deriving it. Pinned
    with a sentinel so this wiring stays covered independently of the resolver's own
    rules — no env var is set here, because the resolver is what reads it."""
    with patch.object(entry, "_resolve_forwarded_allow_ips", return_value="sentinel"):
        run = _run_main()

    assert run.call_args.kwargs["forwarded_allow_ips"] == "sentinel"


# --- tracing: the channels process exports its own spans (#312) ---------------------


@pytest.mark.parametrize(
    ("signoz_enabled", "expected_endpoint"),
    [("true", "http://localhost:14317"), ("false", None)],
)
def test_main_initialises_tracing(
    monkeypatch: pytest.MonkeyPatch,
    tracing_stub: MagicMock,
    signoz_enabled: str,
    expected_endpoint: str | None,
) -> None:
    """The runner starts channels as its own subprocess, so the runner's provider never
    reaches it — without this call every voice STT/TTS span here is a no-op."""
    monkeypatch.setenv("SIGNOZ_ENABLED", signoz_enabled)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:14317")

    _run_main()

    tracing_stub.assert_called_once_with(service_name="channels", endpoint=expected_endpoint)


# Runs in a fresh interpreter: init_tracing installs the process-global tracer provider,
# which OpenTelemetry lets a process set only once. The OTLP exporter class is swapped
# for an in-memory one, so the real init_tracing builds its real pipeline around it.
_EXPORT_PROBE = textwrap.dedent(
    """
    import json
    from unittest.mock import MagicMock, patch

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc import trace_exporter as otlp_grpc
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    otlp_grpc.OTLPSpanExporter = lambda **_kwargs: exporter

    from shared.traced import traced

    # Decorated before main() runs, as the voice modules are at import.
    @traced(name="channels.smoke")
    def work():
        return None

    import core.channels.__main__ as entry

    with (
        patch.object(entry, "create_app", return_value=MagicMock()),
        patch.object(entry.uvicorn, "run"),
    ):
        entry.main()

    work()
    # Without init_tracing this is the API's proxy, which has nothing to flush.
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
    print(json.dumps([
        [span.name, span.resource.attributes["service.name"]]
        for span in exporter.get_finished_spans()
    ]))
    """
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_a_span_from_the_channels_process_is_exported(tmp_path: Path) -> None:
    """End to end through the real init_tracing: a span from a function decorated at
    import time reaches the OTLP exporter tagged with the channels service name."""
    env = {
        **os.environ,
        "SIGNOZ_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:14317",
        "ALFRED_DATA_DIR": str(tmp_path),
        "CHANNELS_PORT": "18081",
    }

    result = subprocess.run(
        [sys.executable, "-c", _EXPORT_PROBE],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    spans = json.loads(result.stdout.strip().splitlines()[-1])
    assert spans == [["channels.smoke", "channels"]]


# --- the seam: _resolve_forwarded_allow_ips() defaults, validates, warns -----------


def _resolve(monkeypatch: pytest.MonkeyPatch, value: str | None) -> tuple[str, MagicMock]:
    """Resolve with a stub logger so warnings can be asserted on."""
    if value is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
    fake_logger = MagicMock()
    monkeypatch.setattr(entry, "logger", fake_logger)
    return entry._resolve_forwarded_allow_ips(), fake_logger


def _warned_entries(fake_logger: MagicMock) -> list[str]:
    """The offending entry passed as the format arg of each per-entry warning."""
    return [str(call.args[1]) for call in fake_logger.warning.call_args_list if len(call.args) > 1]


def _warning_text(fake_logger: MagicMock) -> str:
    """Every warning template, flattened for substring checks."""
    return " ".join(str(call.args[0]) for call in fake_logger.warning.call_args_list)


@pytest.mark.parametrize(
    ("value", "flagged"),
    [
        ("172.18.0.O/16,127.0.0.1", "172.18.0.O/16"),  # letter O for zero
        # uvicorn only wildcards on a bare "*". Inside a list it decays to a literal
        # that matches no IP, silently collapsing trust to loopback only — the most
        # dangerous kind of typo, because it looks like it grants more.
        ("*,127.0.0.1", "*"),
    ],
)
def test_entry_that_cannot_match_an_ip_is_warned(
    monkeypatch: pytest.MonkeyPatch, value: str, flagged: str
) -> None:
    resolved, fake_logger = _resolve(monkeypatch, value)

    assert _warned_entries(fake_logger) == [flagged]
    assert "not a valid IP or CIDR" in _warning_text(fake_logger)
    # Never filtered — uvicorn receives exactly what the operator configured.
    assert resolved == value


def test_bare_wildcard_is_warned_as_a_gate_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whole-value "*" is uvicorn's always_trust: it takes the LEFTMOST
    X-Forwarded-For entry from *any* peer with no validation, so a stranger picks the
    IP the trusted-network gate judges. That is a full perimeter bypass, not the
    "unmatched literal" the per-entry check warns about — it needs its own warning.
    Still passed through: filtering it would silently change what the operator asked
    for, and uvicorn is the one place this value is interpreted."""
    resolved, fake_logger = _resolve(monkeypatch, "*")

    assert resolved == "*"
    text = _warning_text(fake_logger)
    assert "X-Forwarded-For" in text
    assert "trusted-network" in text
    assert "internet-facing" in text


def test_loopback_default_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback can never match a proxy in another container — the operator who
    forgot to set FORWARDED_ALLOW_IPS needs to see why the gate rejects everyone."""
    resolved, fake_logger = _resolve(monkeypatch, None)

    assert resolved == _DEFAULT_FORWARDED_ALLOW_IPS
    assert "will not be rewritten" in _warning_text(fake_logger)


@pytest.mark.parametrize("value", ["172.18.0.5", "172.18.0.5/32,10.0.0.0/8", "::1"])
def test_valid_configuration_warns_about_nothing(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """No false alarms: a well-formed, non-default value stays quiet."""
    resolved, fake_logger = _resolve(monkeypatch, value)

    assert resolved == value
    fake_logger.warning.assert_not_called()
