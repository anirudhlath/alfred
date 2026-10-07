from __future__ import annotations

import logging
import os

import pytest
from evals.harness.display import use_display
from inspect_ai.util import _display as inspect_display
from inspect_ai.util import display_type


@pytest.fixture(autouse=True)
def _restore_inspect_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inspect's display is process-global: put back what the test package pinned."""
    monkeypatch.setattr(inspect_display, "_display_type", None)
    monkeypatch.setenv("INSPECT_DISPLAY", "none")


@pytest.mark.parametrize("display", ["rich", "plain", "none"])
def test_use_display_pins_the_display_before_eval_async_can(display: str) -> None:
    use_display(display)  # type: ignore[arg-type]
    assert display_type() == display and os.environ["INSPECT_DISPLAY"] == display


def test_use_display_warns_when_inspect_already_chose_another(
    caplog: pytest.LogCaptureFixture,
) -> None:
    display_type()  # pins "none" from the environment, as an earlier caller would
    with caplog.at_level(logging.WARNING, logger="evals.harness.display"):
        use_display("rich")
    assert display_type() == "none"
    (warned,) = [r.getMessage() for r in caplog.records]
    assert "--display rich" in warned and "none" in warned
