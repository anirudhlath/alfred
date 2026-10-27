"""The one mapping from a tool's identity to the name the model sees (#299).

A tool's identity is its registry name (``home.light_turn_on``) or its in-process name
(``memory_recall_memories``). Every toolset names its tools through ``wire_name`` and
nothing else rewrites a tool name: the model is offered the wire name, calls it, and the
call is dispatched by it. There is no dotted-name fallback.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Hashable

logger = logging.getLogger(__name__)

MAX_WIRE_NAME: Final = 64
_FORBIDDEN = re.compile(r"[^A-Za-z0-9_-]")

# The problems already logged in this process. Tools are listed on every model request,
# so a broken tool would otherwise log the same ERROR many times a turn.
_reported: set[Hashable] = set()


class WireNameError(ValueError):
    """A tool's identity has no wire name: it is empty, or too long once mapped."""


def wire_name(identity: str) -> str:
    """The name the model sees for the tool *identity*.

    Raises:
        WireNameError: *identity* is empty, or its wire name is over 64 characters.
    """
    if not identity:
        raise WireNameError("a tool name cannot be empty")
    name = _FORBIDDEN.sub("_", identity)
    if len(name) > MAX_WIRE_NAME:
        raise WireNameError(
            f"tool {identity!r} has a {len(name)}-character name; the limit is {MAX_WIRE_NAME}"
        )
    return name


def error_once(key: Hashable, message: str, *args: object) -> None:
    """Log *message* at ERROR the first time this process sees *key*."""
    if key in _reported:
        return
    _reported.add(key)
    logger.error(message, *args)


def report_collision(name: str, kept: str, dropped: str) -> None:
    """Two tools map to wire name *name*: *kept* is offered, *dropped* is not."""
    error_once(
        ("collision", name, dropped),
        "Tools %r and %r both map to wire name %r; %r is not offered",
        kept,
        dropped,
        name,
        dropped,
    )
