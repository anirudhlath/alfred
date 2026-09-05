"""Channels entrypoint — uvicorn is told which proxy to trust for X-Forwarded-*."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, "127.0.0.1"),  # uvicorn's own default: trust loopback only
        ("172.18.0.5", "172.18.0.5"),
        ("172.16.0.0/12,127.0.0.1", "172.16.0.0/12,127.0.0.1"),
    ],
)
def test_main_passes_forwarded_allow_ips(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: str
) -> None:
    import core.channels.__main__ as entry

    if env_value is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", env_value)
    monkeypatch.setenv("CHANNELS_PORT", "18081")

    with (
        patch.object(entry, "create_app", return_value=MagicMock()),
        patch.object(entry.uvicorn, "run") as run,
    ):
        entry.main()

    kwargs = run.call_args.kwargs
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == expected
    assert kwargs["port"] == 18081
