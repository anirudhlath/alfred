"""DAILY_COST_CAP_USD: zero or less is the kill switch; a non-finite amount is an error."""

from __future__ import annotations

import pytest

from shared import config


@pytest.mark.parametrize(("raw", "expected"), [("", 5.0), (" 7.5 ", 7.5), ("0", 0.0), ("-1", -1.0)])
def test_a_finite_cap_parses(raw: str, expected: float) -> None:
    assert config.cost_cap_usd(raw, 5.0) == expected


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "five"])
def test_a_cap_that_is_not_a_finite_amount_names_the_variable(raw: str) -> None:
    with pytest.raises(RuntimeError, match="DAILY_COST_CAP_USD"):
        config.cost_cap_usd(raw, 5.0)


def test_from_env_refuses_a_nan_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_COST_CAP_USD", "nan")
    with pytest.raises(RuntimeError, match="DAILY_COST_CAP_USD"):
        config.AlfredConfig.from_env()
