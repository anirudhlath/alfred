"""Inspect's console display for `alfred evals run`.

Importing this module does not import Inspect, so the CLI can name the choices without it.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

# Inspect's "full" display (its Textual app) is left out: it crashes inside eval_async.
Display = Literal["rich", "plain", "none"]


def use_display(display: Display) -> None:
    """Pin Inspect's console display to *display*.

    The environment variable alone is not enough: ``eval_async`` pins ``plain`` unless a
    display is already set, and Inspect reads ``INSPECT_DISPLAY`` only when asked for one.
    """
    from inspect_ai.util import display_type

    os.environ["INSPECT_DISPLAY"] = display
    if (pinned := display_type()) != display:
        logger.warning(
            "--display %s ignored: Inspect's display was already set to %s", display, pinned
        )
