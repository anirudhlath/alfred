"""The one definition of "is this env flag on?" — shared by the server and the CLI."""

from __future__ import annotations

import pytest

from shared.config import AlfredConfig
from shared.env import is_truthy_flag


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", " 1 ", "\tTrue\n"])
def test_truthy_spellings(value: str) -> None:
    assert is_truthy_flag(value) is True


@pytest.mark.parametrize("value", [None, "", "   ", "0", "false", "no", "off", "2", "y", "on"])
def test_everything_else_is_off(value: str | None) -> None:
    """An unrecognised value never enables anything — a typo must not silently turn a
    security flag on (or, read the other way, leave the operator thinking it is on)."""
    assert is_truthy_flag(value) is False


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, True), ("yes", True), ("TRUE", True), (" 1 ", True), ("false", False), ("2", False)],
)
def test_signoz_enabled_uses_the_one_spelling(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: bool
) -> None:
    """SIGNOZ_ENABLED parsed its own way (``.lower() == "true"``), so ``yes`` — a
    spelling every other Alfred flag honours — silently turned telemetry off. Default
    stays on."""
    if env_value is None:
        monkeypatch.delenv("SIGNOZ_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SIGNOZ_ENABLED", env_value)
    assert AlfredConfig.from_env().signoz_enabled is expected


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, False), ("yes", True), ("TRUE", True), (" 1 ", True), ("false", False), ("2", False)],
)
def test_log_json_uses_the_one_spelling(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: bool
) -> None:
    """Same divergence on the logging side; default stays off."""
    if env_value is None:
        monkeypatch.delenv("LOG_JSON", raising=False)
    else:
        monkeypatch.setenv("LOG_JSON", env_value)
    assert AlfredConfig.from_env().log_json is expected
