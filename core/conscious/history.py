"""System 2's history bounds (spec, component 5): what keeps a session's history, and any
one tool result in it, inside the model's context window.

Measured in production over a week of traces: a model request's input is about 18k tokens
at the median and under 25k at most, nearly all of it tool definitions; a
session holds one or two turns; the largest tool result is an unfiltered live-state read, a
little under 80,000 characters. The smallest window an eval runs on is 32,768 tokens.

The capabilities run cheapest first. ``TruncateToolOutput`` clips a result as it is made, so
the clipped copy is what the model sees and what the session stores. ``ClearToolResults``
blanks old results once the context estimate (tool definitions included)
passes its ceiling, never the most recent pairs. ``SlidingWindowCompaction`` drops the
oldest messages past a count, keeping the first user message and tool call/return pairs
together. Both compactions rewrite the run's messages, which is why a turn replaces the
stored session rather than appending to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic_ai_harness.compaction import ClearToolResults, SlidingWindowCompaction
from pydantic_ai_harness.tool_output_limits import Band, ToolOutputLimits, Truncate

from core.conscious.deps import TurnDeps

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.toolsets import AgentToolset

# Above the largest real result: a ceiling against a runaway one, not a trim.
TOOL_RESULT_MAX_CHARS: Final = 100_000
# Context estimate past which old tool results are blanked; leaves room to answer on a
# 32k-token window.
CLEAR_TOOL_RESULTS_OVER_TOKENS: Final = 28_000
# The most recent tool call/return pairs, never blanked: what a follow-up refers to.
TOOL_RESULTS_KEPT: Final = 3
# Never blank for less than this.
CLEAR_AT_LEAST_TOKENS: Final = 2_000
SESSION_MAX_MESSAGES: Final = 60
SESSION_KEPT_MESSAGES: Final = 40


class TruncateToolOutput(ToolOutputLimits[TurnDeps]):
    """``ToolOutputLimits`` that only truncates. It never spills, so it registers no
    ``read_tool_result`` tool: the catalog stays the agent's one toolset."""

    def get_toolset(self) -> AgentToolset[TurnDeps] | None:
        return None


def history_capabilities() -> list[AbstractCapability[TurnDeps]]:
    """The bounds, cheapest first (see the module docstring)."""
    return [
        TruncateToolOutput(
            bands=[
                Band(over=TOOL_RESULT_MAX_CHARS, action=Truncate(max_chars=TOOL_RESULT_MAX_CHARS))
            ]
        ),
        ClearToolResults(
            max_tokens=CLEAR_TOOL_RESULTS_OVER_TOKENS,
            keep_pairs=TOOL_RESULTS_KEPT,
            min_clear_tokens=CLEAR_AT_LEAST_TOKENS,
        ),
        SlidingWindowCompaction(
            max_messages=SESSION_MAX_MESSAGES, keep_messages=SESSION_KEPT_MESSAGES
        ),
    ]
