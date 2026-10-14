"""The side-effect ledger: what a turn has already done to the world (spec 6, #304).

Every side-effecting tool call is written to the request's ledger before it runs and
settled after it, keyed by ``UserRequest.event_id``, which every delivery of the request
shares. A turn that fails after acting, or a delivery that finds a ledger left by a
crashed one, answers from it with a fixed template (``ledger_reply``) rather than run the
turn again and repeat what it did.

A toolset marks a side-effecting tool with ``SIDE_EFFECT`` in its ``ToolDefinition``
metadata, where it builds the definition; ``RecordEffects`` decides from that alone. A
call refused by identity scope (``Refused``) never acted, so it leaves no entry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from loguru import logger
from pydantic_ai.capabilities import AbstractCapability

from core.conscious.deps import TurnDeps
from core.conscious.tools.catalog import tool_identity
from core.conscious.tools.scope import Refused
from core.routing.domain_router import CONFIRMATION_REQUIRED_PREFIX
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
    """One side-effecting call: ``started`` until it settles as ``ok``, ``failed``, or
    ``parked`` (a critical action held for the user's confirmation, not yet done)."""

    tool: str  # registry / catalog name, e.g. "home.light_turn_on"
    args: dict[str, Any]
    status: EffectStatus


class EffectLedger:
    """Each request's side-effecting calls, in Redis at ``EFFECTS_KEY``."""

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def started(self, request_id: str, tool: str, args: dict[str, Any]) -> int:
        """Record a call about to run; returns its index for ``finished``."""
        key = EFFECTS_KEY.format(request_id)
        entry = json.dumps({"tool": tool, "args": args, "status": "started"}, default=str)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, entry)
            pipe.expire(key, EFFECTS_TTL_S)
            length, _ = await pipe.execute()
        return int(length) - 1

    async def finished(self, request_id: str, index: int, ok: bool) -> None:
        """Settle the call at *index*: it returned (``ok``) or failed."""
        await self._settle(request_id, index, "ok" if ok else "failed")

    async def parked(self, request_id: str, index: int) -> None:
        """Settle the call at *index* as held for the user's confirmation."""
        await self._settle(request_id, index, "parked")

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


def _humanise(tool: str) -> str:
    """``home.light_turn_on`` -> ``light turn on``."""
    return tool.rpartition(".")[2].replace("_", " ")


def _names(effects: list[Effect]) -> str:
    return ", ".join(dict.fromkeys(_humanise(e.tool) for e in effects))


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


def _refused(exc: BaseException) -> bool:
    """Whether *exc* is an identity-scope refusal, raised as itself or wrapped (Pydantic AI
    re-raises a tool's ``ToolFailed`` as ``ToolFailedError`` from it)."""
    return isinstance(exc, Refused) or isinstance(exc.__cause__, Refused)


class RecordEffects(AbstractCapability[TurnDeps]):
    """Records each side-effecting tool call in the turn's ledger, around its execution.

    ``wrap_tool_execute`` runs after argument validation, so a schema retry never reaches
    it. ``started`` is written before the call: if that write fails the call does not run.
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
        ledger = EffectLedger(ctx.deps.services.redis)
        request_id = ctx.deps.request_id
        index = await ledger.started(request_id, tool_identity(tool_def), args)
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
        if isinstance(result, str) and result.startswith(CONFIRMATION_REQUIRED_PREFIX):
            # The router parked a critical action for confirmation: nothing happened yet.
            await _quietly(ledger.parked(request_id, index), request_id)
        else:
            await _quietly(ledger.finished(request_id, index, ok=True), request_id)
        return result


async def _quietly(write: Awaitable[None], request_id: str) -> None:
    try:
        await write
    except Exception as exc:
        logger.warning("Could not settle a ledger entry for request {}: {}", request_id, exc)
