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
from pydantic import BaseModel, field_validator

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
# Deletes each field only while it still holds the bytes ``recent`` read. The state loop and the
# trigger loop share one event loop: a change can be noted between ``recent`` reading an expired
# record and deleting it, and an unconditional HDEL would take the fresh record with it.
_DELETE_IF_UNCHANGED: Final = """
local removed = 0
for i = 1, #ARGV, 2 do
  if redis.call('HGET', KEYS[1], ARGV[i]) == ARGV[i + 1] then
    removed = removed + redis.call('HDEL', KEYS[1], ARGV[i])
  end
end
return removed
"""
# A change by either of these replaces whatever a person did before it.
_SUPERSEDING: Final[frozenset[Cause | None]] = frozenset({"alfred", "automation"})


def _utc(moment: datetime) -> datetime:
    """*moment*, read as UTC when it carries no offset."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class PersonChange(BaseModel):
    """The last change a person made to one entity."""

    time: datetime  # always aware: a stored time with no offset is read as UTC
    cause: Cause | None
    old_state: str | None
    new_state: str
    context_id: str | None
    attributes_only: bool  # old_state == new_state: a brightness or volume, not a state

    @field_validator("time")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _utc(value)


class PersonChanges:
    """The record of what people just changed, one field per entity in a Redis hash."""

    def __init__(self, redis: AioRedis, *, clock: Callable[[], datetime]) -> None:
        self._redis = redis
        self._clock = clock

    async def note(self, event: StateChangedEvent, cause: Cause | None) -> None:
        """Take one state change into the record. Best-effort: never raises.

        A state change by a person, or by one nobody can name (``outside``, or no context at
        all), becomes the entity's record. A change by Alfred or an automation deletes it: that
        change is the latest word on the entity, and what a person did before it is not what
        Reflex would be undoing. An attributes-only change (the old and new state are the same:
        a brightness, a volume) counts only when an HA user made it. The event carries no old
        attributes, so a device report (a bulb or player giving its brightness, position or
        metadata) cannot be told apart from an outside app's slider, and recording one would
        replace a person's change and refresh its time; an attributes-only change with any other
        cause leaves the record as it is. A person's attributes-only change that shares the
        record's context id is the same action reporting itself and leaves the record as it is
        too. An event older than the record is ignored whichever way it would have gone, so a
        replayed old event never overwrites or clears a newer record.
        """
        try:
            attributes_only = event.old_state == event.new_state
            if attributes_only and cause != "person" and cause not in _SUPERSEDING:
                return
            when = _utc(event.timestamp)
            stored = await self._stored(event.entity_id)
            if stored is not None and when < stored.time:
                return
            if cause in _SUPERSEDING:
                await self._redis.hdel(REFLEX_PERSON_CHANGES_KEY, event.entity_id)
                return
            context_id = event.context.id if event.context else None
            if (
                attributes_only
                and stored is not None
                and context_id is not None
                and stored.context_id == context_id
            ):
                return
            change = PersonChange(
                time=when,
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

    async def _stored(self, entity_id: str) -> PersonChange | None:
        """The entity's record, or ``None`` when there is none or it cannot be read."""
        raw = await self._redis.hget(REFLEX_PERSON_CHANGES_KEY, entity_id)
        if raw is None:
            return None
        try:
            return PersonChange.model_validate_json(raw)
        except ValueError:  # an unreadable record is replaced, not trusted
            return None

    async def recent(self, window: timedelta) -> dict[str, PersonChange]:
        """The changes no older than *window*, by entity ID.

        Older ones, and any that cannot be read, are deleted in the same call, each only while
        it still holds what was read: a change noted since stays. A Redis error propagates (fail
        closed), the read's or the prune's: the event stays pending and is retried rather than
        decided blind to a person's change.
        """
        stored: dict[bytes | str, bytes | str] = await self._redis.hgetall(
            REFLEX_PERSON_CHANGES_KEY
        )
        now = self._clock()
        kept: dict[str, PersonChange] = {}
        dropped: list[tuple[bytes | str, bytes | str]] = []
        for field, value in stored.items():
            try:  # only an unreadable record is deleted for it: a clock mismatch must raise
                entity_id = decode_stream_value(field)
                change = PersonChange.model_validate_json(value)
            except ValueError as e:
                logger.warning("Reflex dropped an unreadable person change ({!r}): {}", field, e)
                dropped.append((field, value))
                continue
            if now - change.time > window:
                dropped.append((field, value))
            else:
                kept[entity_id] = change
        if dropped:
            await self._redis.eval(
                _DELETE_IF_UNCHANGED,
                1,
                REFLEX_PERSON_CHANGES_KEY,
                *(part for pair in dropped for part in pair),
            )
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
    """What home-service's ``EntityIndex.resolve`` would act on, within *domain*, case ignored.

    The first step that finds anything decides: the entity IDs equal to the target, else the
    entities in the area it names, else the entities whose friendly name it is.
    """
    wanted = target.strip().casefold()
    entities = [e for e in snapshot.values() if e.domain == domain]
    by_id = [e.entity_id for e in entities if e.entity_id.casefold() == wanted]
    if by_id:
        return sorted(by_id)
    by_area = [
        e.entity_id for e in entities if (area := _area(e.attributes)) and area.casefold() == wanted
    ]
    if by_area:
        return sorted(by_area)
    return sorted(
        e.entity_id for e in entities if _name(e.entity_id, e.attributes).casefold() == wanted
    )


def resolve_targets(tool_name: str, target: str, snapshot: Mapping[str, LiveEntity]) -> list[str]:
    """The entity IDs a tool call's *target* names, or ``[]`` when it names none.

    The target is resolved the way home-service resolves it (``EntityIndex.resolve``), among the
    entities of the tool's domain, ignoring case: as an entity ID, else as an area name, which
    gives every entity of the domain in that area, else as a friendly name. An entity ID in
    another domain names nothing, and a room-named entity does not hide the rest of its room.
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

    - the record is attributes-only (a brightness, a volume) and the action sets attributes, or
      its resulting state is unknown or differs from the one the entity had (fail closed: a
      service not in ``SERVICE_STATES`` has no known state); or
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
        if change.attributes_only:
            return sets_attributes or state != change.new_state  # an unknown state differs too
        differs = state is not None and state != change.new_state
        return differs or (state is None and not sets_attributes)

    return sorted(
        {e for e in _resolve(domain, target, snapshot) if e in changes and reverses(changes[e])}
    )
