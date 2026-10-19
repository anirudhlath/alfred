"""Tool search: how System 2 finds the registry tools that are not loaded.

Registry tools whose audience is not ``reflex`` are deferred (``RegistryToolset``), so a
request carries their names only after the model has searched for them. Pydantic AI's
``ToolSearch`` capability supplies the search; on a chat-completions model it is a local
function tool, ``search_tools``, that takes ``{"queries": [...]}``. See "2. How the model
finds a deferred tool" in docs/superpowers/specs/2026-10-18-dynamic-tool-loading-design.md.

The strategy is pinned: the framework's default is documented as liable to change, and a
change would move which tools a query finds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic_ai.capabilities import ToolSearch

if TYPE_CHECKING:
    from core.conscious.deps import TurnDeps

SEARCH_STRATEGY: Final = "keywords"
SEARCH_MAX_RESULTS: Final = 10
# The framework's own text talks about capability ids, which is wrong here.
SEARCH_DESCRIPTION: Final = (
    "Find a smart-home tool that is not listed yet by describing what it should do. "
    "The listed tools are already loaded. Matching tools can be called from your next step. "
    "Give one short query per need; one call can carry several."
)
QUERIES_DESCRIPTION: Final = "What each needed tool should do, in a few words: one query per need."


def tool_search() -> ToolSearch[TurnDeps]:
    """The capability that lets the model search the turn's deferred tools."""
    return ToolSearch(
        strategy=SEARCH_STRATEGY,
        max_results=SEARCH_MAX_RESULTS,
        tool_description=SEARCH_DESCRIPTION,
        parameter_description=QUERIES_DESCRIPTION,
    )
