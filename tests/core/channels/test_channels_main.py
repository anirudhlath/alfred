"""Channels entrypoint — uvicorn is told which proxy to trust for X-Forwarded-*."""

from __future__ import annotations

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


def test_bare_wildcard_is_accepted_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whole-value "*" is uvicorn's always_trust — blunt, but it does trust every
    peer, so flagging it as unmatched would be a false alarm."""
    resolved, fake_logger = _resolve(monkeypatch, "*")

    assert resolved == "*"
    fake_logger.warning.assert_not_called()


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
