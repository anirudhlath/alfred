"""Importing the engine must leave litellm's own DEBUG handler quiet unless asked.

Regression: ``LITELLM_LOG`` was set after ``import litellm`` (too late — litellm reads it
once at import) and ``configure_logging()`` drops the root logger to NOTSET, so every request
was dumped as one multi-hundred-KB line. With tool results in the messages that exceeded
the supervisor's pipe reader limit and deadlocked the conscious engine mid-turn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import core.conscious.engine  # noqa: F401 — module-level litellm configuration

if TYPE_CHECKING:
    import pytest


def test_litellm_logger_pinned_below_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    lite = logging.getLogger("LiteLLM")
    assert lite.level != logging.NOTSET, "level must be explicit, not inherited from root"
    # configure_logging() sets the root logger to NOTSET; an explicit level must hold anyway.
    monkeypatch.setattr(logging.getLogger(), "level", logging.NOTSET)
    assert not lite.isEnabledFor(logging.DEBUG)
