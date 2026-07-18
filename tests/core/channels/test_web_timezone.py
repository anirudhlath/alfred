"""Tests for client timezone extraction in the web channel."""

from __future__ import annotations

from core.channels.web_server import _resolve_client_timezone


def test_resolves_valid_timezone() -> None:
    assert _resolve_client_timezone({"timezone": "America/Denver"}) == "America/Denver"


def test_rejects_invalid_or_missing() -> None:
    assert _resolve_client_timezone({"timezone": "Not/AZone"}) is None
    assert _resolve_client_timezone({"timezone": 42}) is None
    assert _resolve_client_timezone({}) is None
