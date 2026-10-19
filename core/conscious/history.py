"""System 2's history bounds (spec, component 5): what bounds a session's history, and any
one tool result in it, by message count, old tool results and single-result size.

Measured in production over a week of traces: a model request's input is about 18k tokens
at the median and under 25k at most, nearly all of it tool definitions; a
session holds one or two turns; the largest traced tool result was 187 characters. The
largest result possible, an unfiltered live-state read of about 77,700 characters, was
measured by a probe in the production container, not seen in traces. The smallest window
an eval runs on is 32,768 tokens.

The capabilities run cheapest first. ``TruncateToolOutput`` clips a result as it is made, so
the clipped copy is what the model sees and what the session stores. ``ClearToolResults``
blanks old results once the context estimate
passes its ceiling, never the most recent pairs. The estimate includes tool definitions only
when the history holds a response with provider-reported usage (the harness's anchor); a
converted legacy session or a text-only history is estimated from message text alone, so
clearing can trigger later. And since stored requests carry no instructions, the anchored
estimate counts the current instructions on top of an anchor that already included them
(about 700 tokens high, so it clears slightly early). Both are harmless.
``SlidingWindowCompaction`` drops the
oldest messages past a count, keeping the first user message and tool call/return pairs
together. Both compactions rewrite the run's messages, which is why a turn replaces the
stored session rather than appending to it.

A tool search's call/return pair counts as one of the kept pairs and is never blanked;
SlidingWindowCompaction dropping it unloads the tools it found, and the model searches again.

Nothing bounds total tokens yet, so a history that outgrows the window fails until the
session expires (issue #371). ``ThinkingPart``s are replayed and stored too, and nothing
bounds them; that matters only if a reasoning model is configured (issue #371).
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
# The most recent tool call/return pairs, never blanked: what a follow-up refers to. A tool
# search counts as one pair, so a turn that searches then acts keeps one fewer result.
TOOL_RESULTS_KEPT: Final = 4
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
