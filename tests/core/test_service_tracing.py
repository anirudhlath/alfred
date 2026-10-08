"""Every service process must install its own tracer provider (#312).

The runner starts each service as a separate subprocess, so the provider the runner
installs never reaches them: a service that skips ``init_tracing`` turns every one of its
``@traced`` spans into a no-op. These drive each entry point with ``init_tracing``
stubbed and pin the call — the runner's name for the service and the same SigNoz switch
every other service uses. The channels process is pinned, and its spans followed to the
exporter, in ``channels/test_channels_main.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from shared.config import AlfredConfig

_ENDPOINT = "http://localhost:14317"

# (SIGNOZ_ENABLED, the endpoint init_tracing must receive)
_SIGNOZ_CASES = [("true", _ENDPOINT), ("false", None)]


@pytest.fixture
def otel_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)


class FakeRedis:
    async def aclose(self) -> None:
        return None


@pytest.mark.usefixtures("otel_env")
@pytest.mark.parametrize(("signoz_enabled", "expected_endpoint"), _SIGNOZ_CASES)
def test_bridge_initialises_tracing(
    monkeypatch: pytest.MonkeyPatch, signoz_enabled: str, expected_endpoint: str | None
) -> None:
    import bus.__main__ as bridge_main

    monkeypatch.setenv("SIGNOZ_ENABLED", signoz_enabled)
    init_tracing = MagicMock()

    async def _run_bridge(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(bridge_main, "init_tracing", init_tracing)
    monkeypatch.setattr(bridge_main, "configure_logging", MagicMock())
    monkeypatch.setattr(bridge_main, "run_bridge", _run_bridge)

    bridge_main.main()

    init_tracing.assert_called_once_with(service_name="bridge", endpoint=expected_endpoint)


@pytest.mark.usefixtures("otel_env")
@pytest.mark.parametrize(("signoz_enabled", "expected_endpoint"), _SIGNOZ_CASES)
def test_signal_bridge_initialises_tracing(
    monkeypatch: pytest.MonkeyPatch, signoz_enabled: str, expected_endpoint: str | None
) -> None:
    """Not supervised by the runner, so its name follows its own logging service name."""
    import core.channels.signal_bridge.__main__ as signal_main

    monkeypatch.setenv("SIGNOZ_ENABLED", signoz_enabled)
    init_tracing = MagicMock()

    async def _run(_config: AlfredConfig) -> None:
        return None

    monkeypatch.setattr(signal_main, "init_tracing", init_tracing)
    monkeypatch.setattr(signal_main, "run", _run)

    signal_main.main()

    init_tracing.assert_called_once_with(service_name="signal-bridge", endpoint=expected_endpoint)


@pytest.mark.usefixtures("otel_env")
@pytest.mark.parametrize(("signoz_enabled", "expected_endpoint"), _SIGNOZ_CASES)
def test_memory_ingestor_initialises_tracing(
    monkeypatch: pytest.MonkeyPatch, signoz_enabled: str, expected_endpoint: str | None
) -> None:
    import core.memory.ingestor_main as ingestor_main

    monkeypatch.setenv("SIGNOZ_ENABLED", signoz_enabled)
    init_tracing = MagicMock()

    async def _run(_config: AlfredConfig) -> None:
        return None

    monkeypatch.setattr(ingestor_main, "init_tracing", init_tracing)
    monkeypatch.setattr(ingestor_main, "configure_logging", MagicMock())
    monkeypatch.setattr(ingestor_main, "run", _run)

    ingestor_main.main()

    init_tracing.assert_called_once_with(service_name="memory-ingestor", endpoint=expected_endpoint)


@pytest.mark.usefixtures("otel_env")
@pytest.mark.parametrize(("signoz_enabled", "expected_endpoint"), _SIGNOZ_CASES)
async def test_librarian_initialises_tracing(
    monkeypatch: pytest.MonkeyPatch, signoz_enabled: str, expected_endpoint: str | None
) -> None:
    """The standalone one-shot Librarian, not the in-process scheduler conscious runs."""
    import core.librarian.__main__ as librarian_main

    monkeypatch.setenv("SIGNOZ_ENABLED", signoz_enabled)
    init_tracing = MagicMock()

    def _no_memory(_config: AlfredConfig) -> None:
        raise RuntimeError("no embedding backend")

    monkeypatch.setattr(librarian_main, "init_tracing", init_tracing)
    monkeypatch.setattr(librarian_main, "create_redis", lambda _url: FakeRedis())
    # The early return for a dead memory system is the shortest path through run().
    monkeypatch.setattr(librarian_main, "build_embedding_provider", _no_memory)

    await librarian_main.run()

    init_tracing.assert_called_once_with(service_name="librarian", endpoint=expected_endpoint)
