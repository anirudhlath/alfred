"""Never reverse a person: what they last changed, and what a proposal would act on.

``PersonChanges`` keeps, per entity, the last change a person (or a switch, app or device nobody
can name) made, on ``REFLEX_PERSON_CHANGES_KEY``. ``overridden_entities`` is the rule itself,
hands off: pure, it reads a proposed action, those records and the live snapshot, and names the
entities the action would act on that a person changed within the window, whatever the action
does to them. ``resolved_action`` comes first: it says what a proposal acts on, or that it names
nothing Reflex can check, which fails closed. Rules in code, applied after the model answers,
not prompt rules.

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
from core.reflex.prompt import _area, _head, _name, split_tool
from shared.streams import REFLEX_PERSON_CHANGES_KEY, decode_stream_value

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import timedelta

    from bus.schemas.events import ActionRequest, StateChangedEvent
    from core.reflex.prompt import LiveEntity
    from shared.types import AioRedis

# The parameter a tool call names what it acts on with.
_TARGET: Final = "target"
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
        change is the latest word on the entity, and what a person did before it is no longer
        theirs to be left alone. An attributes-only change (the old and new state are the same:
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
    split = split_tool(tool_name, snapshot)
    return [] if split is None else _resolve(split[0], target, snapshot)


def _by_head(domain: str, target: str, snapshot: Mapping[str, LiveEntity]) -> list[str]:
    """The entities of *domain* the prompt names *target* (``Name (Room)``), case ignored."""
    wanted = target.strip().casefold()
    return sorted(
        e.entity_id
        for e in snapshot.values()
        if e.domain == domain and _head(e.entity_id, e.attributes).casefold() == wanted
    )


def resolved_action(
    action: ActionRequest, snapshot: Mapping[str, LiveEntity]
) -> ActionRequest | None:
    """*action* as home-service would act on it, or ``None`` when it names nothing to act on.

    A target home-service resolves (``resolve_targets``) stands as written. One that names a
    single entity of the tool's domain the way the prompt does (``Name (Room)``, as the What
    changed and Leave alone lines write it) is rewritten to that entity's ID, since
    home-service does not read that form. Anything else is ``None``: no string target, a tool of
    no domain in the snapshot (with no live state, every tool), a target that names nothing in
    the tool's domain, or a ``Name (Room)`` two entities share. What a proposal would act on
    cannot then be checked against what a person just changed, so it fails closed.
    """
    split = split_tool(action.tool_name, snapshot)
    target = action.parameters.get(_TARGET)
    if split is None or not isinstance(target, str) or not target.strip():
        return None
    if _resolve(split[0], target, snapshot):
        return action
    named = _by_head(split[0], target, snapshot)
    if len(named) != 1:
        return None
    return action.model_copy(update={"parameters": {**action.parameters, _TARGET: named[0]}})


def overridden_entities(
    action: ActionRequest,
    changes: Mapping[str, PersonChange],
    snapshot: Mapping[str, LiveEntity],
) -> list[str]:
    """The entities *action* would act on that a person just changed: hands off.

    *changes* is ``PersonChanges.recent()``: the window is already applied. Any action on such an
    entity overrides the person, whatever it would do: undo the change, repeat it, or adjust it
    (a brightness, a volume). *action*'s target is resolved as home-service resolves it, so pass
    it through ``resolved_action`` first: a target it cannot resolve names nothing here.
    """
    target = action.parameters.get(_TARGET)
    if not isinstance(target, str):
        return []
    return [e for e in resolve_targets(action.tool_name, target, snapshot) if e in changes]
