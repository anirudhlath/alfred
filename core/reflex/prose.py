"""Reflex's prose: the intro and decision rules around the data, from the prompt store.

``core.reflex.prompt`` stays pure and only places the prose; this module fetches it. The
store serves from its cache, so the decision path makes no network call once warm.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from core.reflex.prompt import ReflexProse
from shared.prompts.names import REFLEX_RULES, REFLEX_STATE_CHANGE, REFLEX_TRIGGER
from shared.prompts.template import repo_text

if TYPE_CHECKING:
    from shared.prompts.store import PromptStore


def _intro_name(origin: Literal["state_change", "trigger_fired"]) -> str:
    return REFLEX_STATE_CHANGE if origin == "state_change" else REFLEX_TRIGGER


async def fetch_prose(
    store: PromptStore, origin: Literal["state_change", "trigger_fired"]
) -> ReflexProse:
    """The intro for *origin* and the decision rules, as the store serves them."""
    intro, rules = await asyncio.gather(store.get(_intro_name(origin)), store.get(REFLEX_RULES))
    return ReflexProse(intro=intro.text, rules=rules.text)


def repo_prose(origin: Literal["state_change", "trigger_fired"]) -> ReflexProse:
    """The same prose from the repo defaults: no store, no network."""
    return ReflexProse(intro=repo_text(_intro_name(origin)), rules=repo_text(REFLEX_RULES))


async def warm_prose(store: PromptStore) -> None:
    """Fetch all three Reflex prompts into *store*'s cache.

    The first decision after a start would otherwise wait on the store (up to its timeout)
    inside Reflex's latency budget; once warm, ``fetch_prose`` is a cache hit.
    """
    await asyncio.gather(
        *(store.get(name) for name in (REFLEX_STATE_CHANGE, REFLEX_TRIGGER, REFLEX_RULES))
    )
