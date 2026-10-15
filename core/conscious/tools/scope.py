"""Which tools an identity may use: the one rule every toolset applies (spec, component 3).

The check runs when the tools are listed and again when one is called. Finer gating by
``risk_clearance`` replaces ``allowed`` here without touching the toolsets.
"""

from __future__ import annotations

from typing import Final

from pydantic_ai import ToolFailed

SIR: Final = "sir"
# The risk the personal toolsets (memory, attention, integrations) are gated at.
PERSONAL: Final = "personal"


def allowed(risk: str, identity: str) -> bool:
    """Whether *identity* may use a tool of *risk*: the verified user anything, a guest
    only ``benign`` tools."""
    return identity == SIR or risk == "benign"


def refusal(name: str) -> str:
    """What the model is told when *name* is called by someone who may not use it."""
    return f"{name} is only available to the verified user"


class Refused(ToolFailed):
    """A call refused because of who is asking: it never ran, so it changed nothing."""

    def __init__(self, name: str) -> None:
        super().__init__(refusal(name))
