"""System 1's recorded calls become Reflex's decisions, parsed the way Reflex parses them.

Reflex sends the model's reply straight to ``core.reflex.decision.parse_decision``, along
with the reflex-audience tools its prompt showed (``core/reflex/engine.py``). The harness
does the same with the same tools. So an act naming a tool Reflex could not use is
``invalid`` here too.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from core.reflex.decision import parse_decision
from core.reflex.prompt import read_state_change
from evals.harness.checks.llm import message_text
from evals.harness.evidence import ReflexCall, ReflexEvent

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from core.reflex.prompt import RenderedEvent
    from core.reflex.tool_registry import ToolInfo
    from evals.harness.evidence import LlmCall
    from evals.harness.world import World

# core/reflex/prompt.render_now's clock line: "Thu 8 Oct, 22:05 (night)".
_CLOCK = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2}, (\d{2}):\d{2} "
    r"\((?:morning|afternoon|evening|night)\)"
)


def local_hour(messages: list[dict[str, Any]]) -> int | None:
    """The hour Reflex's prompt showed, or None if it had no clock line."""
    for message in messages:
        if (m := _CLOCK.search(message_text(message))) is not None:
            return int(m.group(1))
    return None


def _entity_named(world: World, seen: RenderedEvent) -> str | None:
    """The one world entity Reflex writes this name for: its friendly name, or its id when
    live state names none. The room narrows a name two entities share. None when no one
    entity fits."""
    hits = [e for e in world.entities if seen.name in (e.friendly_name, e.entity_id)]
    if len(hits) > 1 and seen.area is not None:
        rooms = {a.area_id: a.name for a in world.areas}
        hits = [e for e in hits if rooms.get(world.area_of(e.entity_id) or "") == seen.area]
    return hits[0].entity_id if len(hits) == 1 else None


def reflex_event(messages: list[dict[str, Any]], world: World) -> ReflexEvent | None:
    """The state change System 1's prompt was about, or None for a prompt about none (a
    trigger's fire)."""
    for message in messages:
        if (seen := read_state_change(message_text(message))) is not None:
            return ReflexEvent(name=seen.name, state=seen.new, entity_id=_entity_named(world, seen))
    return None


def judges(call: LlmCall, world: World, entity_id: str, state: str) -> bool:
    """Whether *call* is System 1 judging *entity_id* changing to *state*."""
    if call.role != "system1":
        return False
    event = reflex_event(call.messages, world)
    return event is not None and event.is_of(entity_id, state)


def tool_domain(tool: str, domains: Iterable[str]) -> str | None:
    """The HA domain a generated home tool acts on: ``home.light_turn_off`` is ``light``.
    The longest prefix wins, so ``media_player_media_pause`` is ``media_player``."""
    name = tool.removeprefix("home.")
    return max((d for d in domains if name.startswith(f"{d}_")), key=len, default=None)


def targets(world: World, tool: str | None, parameters: dict[str, Any]) -> list[str]:
    """What a proposal would act on, as entity and area ids.

    The ``target`` parameter is resolved the way home-service resolves it
    (``EntityIndex.resolve``), within the tool's domain:
    1. an entity_id;
    2. else an area name;
    3. else an entity's friendly name.

    Each entity's area is added, so a golden can name either the room or the entity. The
    list is in world order (home-service sorts by entity_id); checks test membership.
    """
    raw = parameters.get("target")
    domain = None if tool is None else tool_domain(tool, world.services)
    if domain is None or not isinstance(raw, str) or not raw.strip():
        return []
    wanted = raw.strip().casefold()
    candidates = [e for e in world.entities if not e.disabled and e.domain == domain]
    rooms = [a.area_id for a in world.areas if a.name.casefold() == wanted]
    hits = (
        [e.entity_id for e in candidates if e.entity_id.casefold() == wanted]
        or [entity for room in rooms for entity in world.entities_in(room, domain)]
        or [e.entity_id for e in candidates if e.friendly_name.casefold() == wanted]
    )
    out: list[str] = []
    for entity_id in hits:
        for item in (entity_id, world.area_of(entity_id)):
            if item is not None and item not in out:
                out.append(item)
    return out


def reflex_calls(
    llm_calls: list[LlmCall], tools: Sequence[ToolInfo], world: World
) -> list[ReflexCall]:
    """Every System 1 call, as the decision Reflex took from it, earliest request first.

    The proxy records a call when its reply returns, so a slow call can be recorded after a
    quicker one sent later; checks take ``[0]`` as the earliest request. Each call names
    the state change it was about, so a check judges a step on its own change only.
    """
    out: list[ReflexCall] = []
    for call in sorted(llm_calls, key=lambda c: c.t):
        if call.role != "system1":
            continue
        hour = local_hour(call.messages)
        event = reflex_event(call.messages, world)
        if not 200 <= call.status < 300:
            out.append(
                ReflexCall(
                    t=call.t,
                    latency_ms=call.latency_ms,
                    answered_at=call.answered_at,
                    decision="invalid",
                    problem=f"no reply (HTTP {call.status})",
                    local_hour=hour,
                    event=event,
                )
            )
            continue
        proposal = parse_decision(call.response_text or "", tools)
        action = proposal.action
        tool = None if action is None else action.tool_name
        parameters = {} if action is None else dict(action.parameters)
        out.append(
            ReflexCall(
                t=call.t,
                latency_ms=call.latency_ms,
                answered_at=call.answered_at,
                decision=proposal.decision,
                reason=proposal.reason or "",
                tool=tool,
                parameters=parameters,
                targets=targets(world, tool, parameters),
                problem=proposal.problem,
                local_hour=hour,
                event=event,
            )
        )
    return out
