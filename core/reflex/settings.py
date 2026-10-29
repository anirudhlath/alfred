"""Reflex's settings: one JSON document in Redis, read on every decision.

A missing, unreadable or invalid document reads as the defaults, so a typo in a setting can
never stop Reflex. Each distinct bad value is warned about once for the life of the process:
the document is read for every decision, and the same warning on each would bury the log.

The owner edits the document through the admin API (``PUT /api/admin/reflex/settings``) or
this module's command line:

    docker exec alfred python -m core.reflex.settings show
    docker exec alfred python -m core.reflex.settings set mode ask
    docker exec alfred python -m core.reflex.settings allow-script <id>

Both validate the whole document before they save it, so what they store is what Reflex reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from shared.config import AlfredConfig
from shared.redis_streams import create_redis
from shared.streams import REFLEX_SETTINGS_KEY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shared.types import AioRedis

# The bad documents already warned about (raw, as read), so a repeat read is silent.
_warned: set[bytes | str] = set()

# A clock time of day, 00:00 to 23:59. Part of the model, so a bad time fails the document.
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

# The caps on the two lists, the same as the attention set's admin edit (``admin_api.py``,
# which imports this module and so cannot be imported here).
MAX_LIST_ITEMS = 200
MAX_ITEM_CHARS = 256

# Opaque text the settings only store and compare: never parsed here.
_Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ITEM_CHARS)
]


class ReflexSettings(BaseModel):
    """What the owner can tune about Reflex. Unknown fields are refused, not ignored."""

    model_config = ConfigDict(extra="forbid")

    # "shadow": decisions are recorded and nothing is asked or executed. "ask": a proposal
    # that matches ``ask_tools`` is put to the owner first.
    mode: Literal["shadow", "ask"] = "shadow"
    # fnmatch patterns over tool names: the tools Reflex may ask about. Never parsed here.
    ask_tools: list[_Text] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    # Target ids Reflex may run an approved ask through. Opaque to Reflex: never parsed here.
    allowed_scripts: list[_Text] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    # The numbers are strict: ``true`` or ``"60"`` is a wrong type, not 1 or 60. A flag read as 1
    # would shrink the never-reverse window to a minute. (A whole number is still a float.)
    # How long after a person changes something Reflex will not propose moving it back.
    reverse_window_minutes: int = Field(60, ge=1, le=1440, strict=True)
    # How long an ask waits for an answer before it lapses.
    ask_expiry_seconds: int = Field(600, ge=60, le=3600, strict=True)
    # How long after asking about something Reflex will not ask about it again.
    repeat_hours: float = Field(2.0, ge=0, le=168, strict=True)
    # The most asks in one local day.
    daily_ask_cap: int = Field(6, ge=0, le=50, strict=True)
    # Quiet hours, local time: no ask in this window. Equal start and end means none.
    quiet_hours_start: str = Field("23:00", pattern=HHMM_PATTERN)
    quiet_hours_end: str = Field("07:00", pattern=HHMM_PATTERN)


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'document'}: {err['msg']}"
        for err in error.errors()
    )


async def load_settings(redis: AioRedis) -> ReflexSettings:
    """The stored settings, or the defaults when there are none or they cannot be used.

    A Redis error is not caught here: it is not a bad document.
    """
    raw: bytes | str | None = await redis.get(REFLEX_SETTINGS_KEY)
    if raw is None:
        return ReflexSettings()
    try:
        return ReflexSettings.model_validate_json(raw)
    except ValidationError as e:
        if raw not in _warned:
            _warned.add(raw)
            logger.warning("Reflex settings unusable, using the defaults: {}", _problems(e))
        return ReflexSettings()


async def save_settings(redis: AioRedis, settings: ReflexSettings) -> None:
    """Store *settings* as the document ``load_settings`` reads."""
    await redis.set(REFLEX_SETTINGS_KEY, settings.model_dump_json())


async def _stored(redis: AioRedis) -> tuple[ReflexSettings, str | None]:
    """What Reflex reads and, when a document is stored but unusable, what is wrong with it.

    A missing document is the defaults and no problem; an unusable one is the defaults too (as
    ``load_settings`` has it) with its problems.
    """
    raw: bytes | str | None = await redis.get(REFLEX_SETTINGS_KEY)
    if raw is None:
        return ReflexSettings(), None
    try:
        return ReflexSettings.model_validate_json(raw), None
    except ValidationError as e:
        return ReflexSettings(), _problems(e)


class SettingsUnusableError(ValueError):
    """The stored document is not usable settings; ``problems`` says what is wrong with it."""

    def __init__(self, problems: str) -> None:
        super().__init__(f"the stored settings are unusable: {problems}")
        self.problems = problems


async def add_allowed_scripts(
    redis: AioRedis, ids: Sequence[str]
) -> tuple[ReflexSettings, list[str]]:
    """Add *ids* to ``allowed_scripts``, each once: the settings now stored and the ids added.

    The one road a script is allowed by, for the command line's ``allow-script`` and for the
    owner's yes to a script ask. A plain read, change and save of the whole document (not
    atomic: a concurrent edit of the same document in the milliseconds between can be lost).
    Nothing is saved, and the error is raised, when the stored document is unusable
    (``SettingsUnusableError``: writing the defaults over it would lose what the owner wrote) or the
    addition would make it invalid (a ``ValidationError``: the list past its cap, a blank id).
    """
    current, problems = await _stored(redis)
    if problems is not None:
        raise SettingsUnusableError(problems)
    added = [i for i in dict.fromkeys(i.strip() for i in ids) if i not in current.allowed_scripts]
    if not added:
        return current, []
    updated = ReflexSettings.model_validate(
        {**current.model_dump(), "allowed_scripts": [*current.allowed_scripts, *added]}
    )
    await save_settings(redis, updated)
    return updated, added


# --- the command line ---------------------------------------------------------------------

_PROG = "python -m core.reflex.settings"
_UNUSABLE = (
    "the stored document is unusable ({}), so Reflex reads the defaults; replace it with"
    " PUT /api/admin/reflex/settings, and use `show` to see what Reflex reads"
)
Command = Literal["show", "set", "allow-script", "disallow-script"]


@dataclass(frozen=True)
class Args:
    """One command: ``set`` carries a field and a value, the script commands an id."""

    command: Command
    field: str = ""
    value: str = ""


def parse_args(argv: Sequence[str] | None = None) -> Args:
    """The command line, or a usage error (exit status 2)."""
    parser = argparse.ArgumentParser(prog=_PROG, description="Show or change Reflex's settings.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="print the settings Reflex reads")
    setter = commands.add_parser(
        "set",
        help="change one field",
        description=(
            "Change one field. The value is read as JSON when it parses (3, 0.5, "
            '\'["a*", "b"]\'), else as a string (ask, 22:30). The whole document is '
            "checked first: an invalid one is refused and nothing is saved."
        ),
    )
    setter.add_argument("field", help="one of: " + ", ".join(ReflexSettings.model_fields))
    setter.add_argument("value")
    for name, text in (
        ("allow-script", "add an id to allowed_scripts (no change if it is there)"),
        ("disallow-script", "remove an id from allowed_scripts (no change if it is not there)"),
    ):
        commands.add_parser(name, help=text).add_argument("id")
    ns = parser.parse_args(argv)
    match ns.command:
        case "set":
            return Args("set", ns.field, ns.value)
        case "allow-script" | "disallow-script":
            return Args(ns.command, value=ns.id)
        case _:
            return Args("show")


def _read_value(text: str) -> object:
    """*text* as JSON when it parses, else as the string it is."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _refuse(what: str, error: ValidationError) -> int:
    print(f"{_PROG}: {what} refused, nothing saved: {_problems(error)}", file=sys.stderr)
    return 1


async def _store(redis: AioRedis, current: ReflexSettings, changes: dict[str, object]) -> int:
    """Validate *current* with *changes* applied, then save it: the one road a change takes."""
    try:
        updated = ReflexSettings.model_validate({**current.model_dump(), **changes})
    except ValidationError as e:
        return _refuse(", ".join(changes), e)
    await save_settings(redis, updated)
    print(updated.model_dump_json(indent=2))
    return 0


async def execute(redis: AioRedis, args: Args) -> int:
    """Run one command against *redis*; the exit status (0 done, 1 refused).

    ``show`` prints what Reflex reads. The commands that change the document refuse to edit one
    that is stored but unusable: they would write the defaults over it with the change on top,
    and the owner would lose what was there without seeing it.
    """
    current, problems = await _stored(redis)
    if args.command == "show":
        print(current.model_dump_json(indent=2))
        if problems is not None:
            print(f"{_PROG}: {_UNUSABLE.format(problems)}", file=sys.stderr)
        return 0
    if problems is not None:
        print(f"{_PROG}: {_UNUSABLE.format(problems)}; nothing saved", file=sys.stderr)
        return 1
    match args.command:
        case "set":
            return await _store(redis, current, {args.field: _read_value(args.value)})
        case "allow-script":
            wanted = args.value.strip()
            try:
                updated, added = await add_allowed_scripts(redis, [wanted])
            except ValidationError as e:
                return _refuse("allowed_scripts", e)
            except SettingsUnusableError as e:  # only a write since the read above gets here
                print(f"{_PROG}: {_UNUSABLE.format(e.problems)}; nothing saved", file=sys.stderr)
                return 1
            if not added:
                print(f"{wanted} is already in allowed_scripts; nothing to do")
                return 0
            print(updated.model_dump_json(indent=2))
            return 0
        case "disallow-script":
            wanted = args.value.strip()
            if wanted not in current.allowed_scripts:
                print(f"{wanted} is not in allowed_scripts; nothing to do")
                return 0
            kept = [script for script in current.allowed_scripts if script != wanted]
            return await _store(redis, current, {"allowed_scripts": kept})


async def _main(args: Args) -> int:
    redis = create_redis(AlfredConfig.from_env().redis_url)
    try:
        return await execute(redis, args)
    finally:
        await redis.aclose()


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_main(parse_args(argv))))


if __name__ == "__main__":
    main()
