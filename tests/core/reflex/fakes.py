"""A Reflex agent over a scripted model, for tests that need an engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.reflex.agent import build_reflex_agent
from tests.llm_fakes import Script

if TYPE_CHECKING:
    from core.reflex.agent import ReflexAgent
    from shared.config import ReflexOutput
    from tests.llm_fakes import Reply


def reflex_agent(*replies: Reply, output: ReflexOutput = "text") -> tuple[ReflexAgent, Script]:
    """The Reflex agent, asking for its reply in *output*, over a ``Script`` that answers in order.

    The script records every request, so a test reads the prompt the engine sent from
    ``script.prompt()`` and counts model calls with ``script.call_count``. A reply is the
    text for ``text``; for the other shapes it is the reply's dict, which the script sends as
    the output tool's arguments (``tool``) or as JSON text.
    """
    script = Script(*replies, json_schema_output=output != "tool")
    return build_reflex_agent(script.model, output), script
