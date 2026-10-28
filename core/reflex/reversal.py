"""Never reverse a person: what they last changed, and which entities a proposal would undo.

``PersonChanges`` keeps, per entity, the last change a person (or a switch, app or device nobody
can name) made, on ``REFLEX_PERSON_CHANGES_KEY``. ``reversed_entities`` is the rule itself: pure,
it reads a proposed action, those records and the live snapshot, and says which entities the
action would move back. A rule in code, applied after the model answers, not a prompt rule.

The tool names that reach it are whatever the registry holds. It reads a tool's HA domain and
service off the end of the name (``<anything>.<domain>_<service>``) against the domains the
snapshot has, and names no tool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from loguru import logger
from pydantic import BaseModel

from core.attribution.cause import Cause  # noqa: TC001  # pydantic field type
from core.reflex.prompt import _area, _name
from shared.streams import REFLEX_PERSON_CHANGES_KEY, decode_stream_value

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import timedelta

    from bus.schemas.events import ActionRequest, StateChangedEvent
    from core.reflex.prompt import LiveEntity
    from shared.types import AioRedis

# The state each HA service leaves an entity in. A service not listed here has no known state.
SERVICE_STATES: Final = {
    "turn_on": "on",
    "turn_off": "off",
    "media_play": "playing",
    "media_pause": "paused",
}
# What an action carries when it sets an attribute (a brightness, a volume) rather than a state.
_ATTRIBUTE_SERVICES: Final = frozenset({"volume_set"})
_ATTRIBUTE_PARAMETER: Final = "brightness_pct"
# A change by either of these replaces whatever a person did before it.
_SUPERSEDING: Final[frozenset[Cause | None]] = frozenset({"alfred", "automation"})


class PersonChange(BaseModel):
    """The last change a person made to one entity."""

    time: datetime
    cause: Cause | None
    old_state: str | None
    new_state: str
    context_id: str | None
    attributes_only: bool  # old_state == new_state: a brightness or volume, not a state


class PersonChanges:
    """The record of what people just changed, one field per entity in a Redis hash."""

    def __init__(self, redis: AioRedis, *, clock: Callable[[], datetime]) -> None:
        self._redis = redis
        self._clock = clock

    async def note(self, event: StateChangedEvent, cause: Cause | None) -> None:
        """Take one state change into the record. Best-effort: never raises.

        A person's change, or one nobody can name (``outside``, or no context at all), becomes
        the entity's record. A change by Alfred or an automation deletes it: that change is the
        latest word on the entity, and what a person did before it is not what Reflex would
        be undoing. An attributes-only change that shares the record's context id is the same
        action reporting itself (a bulb giving its brightness after being turned on) and
        leaves the record as it is.
        """
        try:
            if cause in _SUPERSEDING:
                await self._redis.hdel(REFLEX_PERSON_CHANGES_KEY, event.entity_id)
                return
            context_id = event.context.id if event.context else None
            attributes_only = event.old_state == event.new_state
            if attributes_only and await self._is_same_action(event.entity_id, context_id):
                return
            when = event.timestamp
            change = PersonChange(
                time=when if when.tzinfo else when.replace(tzinfo=UTC),
                cause=cause,
                old_state=event.old_state,
                new_state=event.new_state,
                context_id=context_id,
                attributes_only=attributes_only,
            )
            await self._redis.hset(
                REFLEX_PERSON_CHANGES_KEY, event.entity_id, change.model_dump_json()
            )
        except Exception as e:
            logger.warning(
                "Reflex could not note the change to {} ({}): {}", event.entity_id, cause, e
            )

    async def _is_same_action(self, entity_id: str, context_id: str | None) -> bool:
        """Whether the entity's record came from the action with this context id."""
        if context_id is None:
            return False
        raw = await self._redis.hget(REFLEX_PERSON_CHANGES_KEY, entity_id)
        if raw is None:
            return False
        try:
            return PersonChange.model_validate_json(raw).context_id == context_id
        except ValueError:  # an unreadable record is replaced, not trusted
            return False

    async def recent(self, window: timedelta) -> dict[str, PersonChange]:
        """The changes no older than *window*, by entity ID.

        Older ones, and any that cannot be read, are deleted in the same call. A Redis error
        propagates (fail closed): the event stays pending and is retried rather than decided
        blind to a person's change.
        """
        stored: dict[bytes | str, bytes | str] = await self._redis.hgetall(
            REFLEX_PERSON_CHANGES_KEY
        )
        now = self._clock()
        kept: dict[str, PersonChange] = {}
        dropped: list[bytes | str] = []
        for field, value in stored.items():
            try:
                entity_id = decode_stream_value(field)
                change = PersonChange.model_validate_json(value)
                age = now - change.time
            except (ValueError, TypeError) as e:
                logger.warning("Reflex dropped an unreadable person change ({!r}): {}", field, e)
                dropped.append(field)
                continue
            if age > window:
                dropped.append(field)
            else:
                kept[entity_id] = change
        if dropped:
            await self._redis.hdel(REFLEX_PERSON_CHANGES_KEY, *dropped)
        return kept


def _split_tool(tool_name: str, snapshot: Mapping[str, LiveEntity]) -> tuple[str, str] | None:
    """The tool's HA domain and service, or ``None`` when no snapshot domain starts its name.

    The domain is the longest one in the snapshot that the tool's last dotted segment starts
    with (``media_player_media_pause`` is ``media_player``, not a shorter prefix of it).
    """
    segment = tool_name.rsplit(".", 1)[-1]
    domains = {entity.domain for entity in snapshot.values()}
    domain = max((d for d in domains if segment.startswith(f"{d}_")), key=len, default=None)
    return None if domain is None else (domain, segment.removeprefix(f"{domain}_"))


def _resolve(domain: str, target: str, snapshot: Mapping[str, LiveEntity]) -> list[str]:
    target = target.strip()
    if target in snapshot:
        return [target]
    wanted = target.casefold()
    entities = [e for e in snapshot.values() if e.domain == domain]
    named = [e.entity_id for e in entities if _name(e.entity_id, e.attributes).casefold() == wanted]
    if named:
        return sorted(named)
    return sorted(
        e.entity_id for e in entities if (area := _area(e.attributes)) and area.casefold() == wanted
    )


def resolve_targets(tool_name: str, target: str, snapshot: Mapping[str, LiveEntity]) -> list[str]:
    """The entity IDs a tool call's *target* names, or ``[]`` when it names none.

    The target is read as an entity ID in the snapshot, then as the friendly name of one of the
    tool's domain's entities, then as an area name, which gives that domain's entities in it
    (names and areas ignore case).
    """
    split = _split_tool(tool_name, snapshot)
    return [] if split is None else _resolve(split[0], target, snapshot)


def reversed_entities(
    action: ActionRequest,
    changes: Mapping[str, PersonChange],
    snapshot: Mapping[str, LiveEntity],
) -> list[str]:
    """The entities *action* would move back from what a person just changed them to.

    *changes* is ``PersonChanges.recent()``: the window is already applied. A target with a
    record is reversed when:

    - the record is attributes-only (a brightness, a volume) and the action sets attributes or
      leaves the entity in a state other than the one it had; or
    - the record is a state change and the action's state is known and differs from the record's;
      or
    - the record is a state change and the action is of a kind not known (its service is neither
      in ``SERVICE_STATES`` nor an attribute setter): fail closed.
    """
    split = _split_tool(action.tool_name, snapshot)
    target = action.parameters.get("target")
    if split is None or not isinstance(target, str):
        return []
    domain, service = split
    state = SERVICE_STATES.get(service)
    sets_attributes = (
        service in _ATTRIBUTE_SERVICES or action.parameters.get(_ATTRIBUTE_PARAMETER) is not None
    )

    def reverses(change: PersonChange) -> bool:
        differs = state is not None and state != change.new_state
        if change.attributes_only:
            return sets_attributes or differs
        return differs or (state is None and not sets_attributes)

    return sorted(
        {e for e in _resolve(domain, target, snapshot) if e in changes and reverses(changes[e])}
    )
