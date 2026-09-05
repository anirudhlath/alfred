"""The one definition of "is this env flag on?" — shared by the server and the CLI."""

from __future__ import annotations

import pytest

from shared.env import is_truthy_flag


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", " 1 ", "\tTrue\n"])
def test_truthy_spellings(value: str) -> None:
    assert is_truthy_flag(value) is True


@pytest.mark.parametrize("value", [None, "", "   ", "0", "false", "no", "off", "2", "y", "on"])
def test_everything_else_is_off(value: str | None) -> None:
    """An unrecognised value never enables anything — a typo must not silently turn a
    security flag on (or, read the other way, leave the operator thinking it is on)."""
    assert is_truthy_flag(value) is False
