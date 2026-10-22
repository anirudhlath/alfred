"""The side-effect ledger: what a turn has already done to the world (spec 6, #304).

Every side-effecting tool call is written to the request's ledger before it runs and
settled after it, keyed by ``UserRequest.event_id``, which every delivery of the request
shares. A turn that fails after acting, or a delivery that finds a ledger left by a
crashed one, answers from it with a fixed template (``ledger_reply``) rather than run the
turn again and repeat what it did.

A toolset marks a side-effecting tool with ``SIDE_EFFECT`` in its ``ToolDefinition``
metadata, where it builds the definition; ``RecordEffects`` decides from that alone. A
call refused by identity scope (``Refused``) never acted, so it leaves no entry.

The replay window (``MAX_REPLAY_AGE_MS``, 300 s) is checked again when a side-effecting
call is about to run, not only when a delivery starts: a slow turn that reaches "unlock
the door" five minutes after it was asked is refused (``TooLate``), and the model is told
why. The age is measured from ``TurnDeps.received_ms``, the request's stream entry id,
the same clock the runner's window reads.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from loguru import logger
from pydantic_ai import ToolFailed
from pydantic_ai.capabilities import AbstractCapability

from core.conscious.deps import TurnDeps
from core.conscious.tools.catalog import tool_identity
from core.conscious.tools.scope import Refused
from shared.redis_streams import MAX_REPLAY_AGE_MS
from shared.streams import EFFECTS_KEY

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from pydantic_ai import RunContext, ToolDefinition
    from pydantic_ai.capabilities import ValidatedToolArgs, WrapToolExecuteHandler
    from pydantic_ai.messages import ToolCallPart

    from shared.types import AioRedis

__all__ = [
    "EFFECTS_TTL_S",
    "SIDE_EFFECT",
    "Effect",
    "EffectLedger",
    "RecordEffects",
    "TooLate",
    "humanise",
    "ledger_reply",
]

# Outlives the replay window many times over, as the answered marker does.
EFFECTS_TTL_S: Final = 3600
# The ToolDefinition metadata key a toolset sets on each side-effecting tool.
SIDE_EFFECT: Final = "side_effect"

EffectStatus = Literal["started", "ok", "failed", "parked"]
_STATUSES: Final[frozenset[str]] = frozenset({"started", "ok", "failed", "parked"})
# A refused call's entry: kept in place, so no other entry's index moves, and never read.
_REFUSED: Final = "refused"


@dataclass(frozen=True)
class Effect:
    """One side-effecting call: ``started`` until it settles as ``ok`` or ``failed``; or
    ``parked``, a critical call held for the user's confirmation, which never ran."""

    tool: str  # registry / catalog name, e.g. "home.light_turn_on"
    args: dict[str, Any]
    status: EffectStatus


class EffectLedger:
    """Each request's side-effecting calls, in Redis at ``EFFECTS_KEY``."""

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def started(self, request_id: str, tool: str, args: dict[str, Any]) -> int:
        """Record a call about to run; returns its index for ``finished``."""
        return await self._append(request_id, tool, args, "started")

    async def parked_call(self, request_id: str, tool: str, args: dict[str, Any]) -> int:
        """Record a call held for the user's confirmation, in one write: it never ran, so
        there is no ``started`` entry to strand in doubt if the process dies between two."""
        return await self._append(request_id, tool, args, "parked")

    async def _append(
        self, request_id: str, tool: str, args: dict[str, Any], status: EffectStatus
    ) -> int:
        key = EFFECTS_KEY.format(request_id)
        entry = json.dumps({"tool": tool, "args": args, "status": status}, default=str)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, entry)
            pipe.expire(key, EFFECTS_TTL_S)
            length, _ = await pipe.execute()
        return int(length) - 1

    async def finished(self, request_id: str, index: int, ok: bool) -> None:
        """Settle the call at *index*: it returned (``ok``) or failed."""
        await self._settle(request_id, index, "ok" if ok else "failed")

    async def discard(self, request_id: str, index: int) -> None:
        """Strike the call at *index* from what ``read`` returns: it was refused, so it
        never acted."""
        await self._settle(request_id, index, _REFUSED)

    async def read(self, request_id: str) -> list[Effect]:
        """The request's calls in the order they started; empty if it made none."""
        raw: list[bytes | str] = await self._redis.lrange(EFFECTS_KEY.format(request_id), 0, -1)  # type: ignore[misc,unused-ignore]
        effects: list[Effect] = []
        for item in raw:
            entry = json.loads(item)
            if entry.get("status") in _STATUSES:
                status = cast("EffectStatus", entry["status"])
                effects.append(Effect(str(entry["tool"]), dict(entry["args"]), status))
        return effects

    async def _settle(self, request_id: str, index: int, status: str) -> None:
        key = EFFECTS_KEY.format(request_id)
        raw: bytes | str | None = await self._redis.lindex(key, index)  # type: ignore[misc,unused-ignore]
        if raw is None:
            logger.warning("No ledger entry {} for request {} to settle", index, request_id)
            return
        entry = json.loads(raw)
        entry["status"] = status
        await self._redis.lset(key, index, json.dumps(entry))  # type: ignore[misc,unused-ignore]


def humanise(tool: str) -> str:
    """``home.light_turn_on`` -> ``light turn on``."""
    return tool.rpartition(".")[2].replace("_", " ")


def _names(effects: list[Effect]) -> str:
    return ", ".join(dict.fromkeys(humanise(e.tool) for e in effects))


def ledger_reply(effects: list[Effect]) -> str:
    """The answer to a turn that acted and then could not finish: what it did, and what
    may or may not have happened. A fixed template; no model call."""
    done = [e for e in effects if e.status == "ok"]
    parked = [e for e in effects if e.status == "parked"]
    in_doubt = [e for e in effects if e.status in ("started", "failed")]
    if done:
        text = (
            f"I'd started on that — I did: {_names(done)} — then ran into a problem before I "
            "could finish, sir."
        )
    else:
        text = "I ran into a problem partway through, sir."
    if in_doubt:
        text += (
            f" I'm not certain these went through: {_names(in_doubt)}. "
            "Please check before asking again."
        )
    if parked:
        text += f" I've asked for your confirmation before: {_names(parked)}."
    return text


class TooLate(ToolFailed):
    """A side-effecting call refused because its request is older than the replay window:
    it never ran, so it changed nothing."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} was not run: this request was made more than "
            f"{MAX_REPLAY_AGE_MS // 60_000} minutes ago, and acting on it this late could be "
            "unsafe. Tell the user it was not done; they can ask again if they still want it."
        )


def _refused(exc: BaseException) -> bool:
    """Whether *exc* is an identity-scope refusal, raised as itself or wrapped (Pydantic AI
    re-raises a tool's ``ToolFailed`` as ``ToolFailedError`` from it)."""
    return isinstance(exc, Refused) or isinstance(exc.__cause__, Refused)


class RecordEffects(AbstractCapability[TurnDeps]):
    """Records each side-effecting tool call in the turn's ledger, around its execution.

    ``wrap_tool_execute`` runs after argument validation, so a schema retry never reaches
    it. A call whose request is older than the replay window is refused (``TooLate``)
    before anything is written: it never runs, and reads are never refused.
    ``started`` is written before the call: if that write fails the call does not run.
    A failure to settle the entry afterwards is logged and leaves it ``started``, which
    reads as in doubt — never as not done.
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[TurnDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        if not (tool_def.metadata or {}).get(SIDE_EFFECT):
            return await handler(args)
        tool = tool_identity(tool_def)
        request_id = ctx.deps.request_id
        age_ms = int(time.time() * 1000) - ctx.deps.received_ms
        if age_ms > MAX_REPLAY_AGE_MS:
            logger.warning(
                "Refused {} for request {}: it is {} s old, past the {} s replay window",
                tool,
                request_id,
                age_ms // 1000,
                MAX_REPLAY_AGE_MS // 1000,
            )
            raise TooLate(tool)
        ledger = EffectLedger(ctx.deps.services.redis)
        index = await ledger.started(request_id, tool, args)
        try:
            result = await handler(args)
        except BaseException as exc:
            # Cancellation and control flow land here too, on purpose: whether the call
            # acted is unknown, so it is recorded failed, which reads as in doubt.
            if _refused(exc):
                await _quietly(ledger.discard(request_id, index), request_id)
            else:
                await _quietly(ledger.finished(request_id, index, ok=False), request_id)
            raise
        await _quietly(ledger.finished(request_id, index, ok=True), request_id)
        return result


async def _quietly(write: Awaitable[None], request_id: str) -> None:
    try:
        await write
    except Exception as exc:
        logger.warning("Could not settle a ledger entry for request {}: {}", request_id, exc)
