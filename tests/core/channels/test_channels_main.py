"""Channels entrypoint — uvicorn is told which proxy to trust for X-Forwarded-*."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import core.channels.__main__ as entry
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
        (None, "127.0.0.1"),  # uvicorn's own default: trust loopback only
        # Blank is how .env.example ships keys and what compose injects for an unset
        # key. It must NOT reach uvicorn — a blank trusts nothing, so X-Forwarded-*
        # is never applied and the gate sees the proxy instead of the real client.
        ("", "127.0.0.1"),
        ("   ", "127.0.0.1"),
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


def _warnings(fake_logger: MagicMock) -> str:
    """Every warning template plus its format args, flattened for substring checks."""
    return " ".join(
        " ".join(str(arg) for arg in call.args) for call in fake_logger.warning.call_args_list
    )


def test_malformed_entry_is_warned_but_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd entry can never match a peer, so uvicorn silently ignores the
    proxy's headers. Say so — but hand uvicorn the raw string regardless."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "172.18.0.O/16,127.0.0.1")
    fake_logger = MagicMock()
    monkeypatch.setattr(entry, "logger", fake_logger)

    run = _run_main()

    warned = _warnings(fake_logger)
    assert "not a valid IP or CIDR" in warned
    assert "172.18.0.O/16" in warned
    # Not filtered — uvicorn still receives exactly what the operator configured.
    assert run.call_args.kwargs["forwarded_allow_ips"] == "172.18.0.O/16,127.0.0.1"


def test_loopback_default_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback can never match a proxy in another container — the operator who
    forgot to set FORWARDED_ALLOW_IPS needs to see why the gate rejects everyone."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    fake_logger = MagicMock()
    monkeypatch.setattr(entry, "logger", fake_logger)

    _run_main()

    assert "will not be rewritten" in _warnings(fake_logger)


@pytest.mark.parametrize("value", ["172.18.0.5", "172.18.0.5/32,10.0.0.0/8", "*"])
def test_valid_configuration_warns_about_nothing(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """No false alarms: a well-formed value (including uvicorn's "*") stays quiet."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
    fake_logger = MagicMock()
    monkeypatch.setattr(entry, "logger", fake_logger)

    _run_main()

    fake_logger.warning.assert_not_called()
