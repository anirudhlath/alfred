"""Daily spend on the harness's ``SpendLimits``: one budget, shared by every process.

Each process builds one ``SpendLimits`` with ``build_spend_limits`` and hands it to every
agent that calls a paid model (System 2 and the Librarian's agents). Its counter is a
``RedisSpendStore``, which applies each response in one Lua script, so concurrent
writers in different processes cannot lose an update. See "7. Spend" in
docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md.

- ``price`` makes a self-hosted response free and a paid model the registry does not
  know expensive, so an unpriced model can never slip under the cap.
- ``TolerantSpendStore`` turns a failed write into a WARNING. The response was already
  billed; failing the turn would replay it, and its tool calls, for a bookkeeping error.
  A failed read still raises, so the gate fails closed.
- ``alert_if_needed`` sends the 80% alert once a day across processes (``SET NX``).
- ``spend_snapshot`` is the admin overview's ``cost`` object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from pydantic_ai_harness.spend import Budget, RedisSpendStore, SpendLimits, Spent

from shared.streams import SPEND_ALERT_KEY, SPEND_PREFIX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from pydantic_ai.messages import ModelResponse
    from pydantic_ai_harness.spend import BatchSpendStore, BudgetStatus, SpendEntry

    from shared.types import AioRedis

logger = logging.getLogger(__name__)

FALLBACK_INPUT_USD_PER_MTOK: Final = Decimal("3")
FALLBACK_OUTPUT_USD_PER_MTOK: Final = Decimal("15")
ALERT_FRACTION: Final = 0.8

BUDGET_NAME: Final = "daily"
# The notification source the 80% alert carries.
ALERT_SOURCE: Final = "spend"
# Twice the day, so a marker outlives any skew between the processes' clocks.
_ALERT_TTL_S: Final = 172800
_FREE_PROVIDERS: Final = frozenset({"vllm"})
_PER_MTOK: Final = Decimal(1_000_000)


def price(response: ModelResponse) -> Decimal | None:
    """What *response* cost: $0 self-hosted, the genai-prices price when the registry knows
    the model, else the fallback rates with one WARNING per model."""
    if response.provider_name in _FREE_PROVIDERS:
        return Decimal(0)
    if response.model_name:
        try:
            return response.cost().total_price
        except (LookupError, ValueError):
            # LookupError: not in the registry. ValueError: it rejects the usage shape.
            pass
    model = response.model_name or "<unnamed>"
    _warn_unpriced(model)
    usage = response.usage
    return (
        Decimal(usage.input_tokens) * FALLBACK_INPUT_USD_PER_MTOK
        + Decimal(usage.output_tokens) * FALLBACK_OUTPUT_USD_PER_MTOK
    ) / _PER_MTOK


@cache
def _warn_unpriced(model: str) -> None:
    logger.warning(
        "No price for model %s; charging it the fallback $%s/$%s per million input/output "
        "tokens against the daily budget",
        model,
        FALLBACK_INPUT_USD_PER_MTOK,
        FALLBACK_OUTPUT_USD_PER_MTOK,
    )


@dataclass
class TolerantSpendStore:
    """A store whose failed writes log and under-count instead of failing the turn.

    ``SpendLimits`` raises a write error from the model request once the response is in,
    which would discard a reply the provider already billed and replay the request.
    Reads pass through untouched: a read that fails refuses the request.
    """

    inner: BatchSpendStore

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Spent]:
        return await self.inner.get_many(keys)

    async def add_many(self, entries: Sequence[SpendEntry]) -> Mapping[str, Spent]:
        try:
            return await self.inner.add_many(entries)
        except Exception:
            logger.warning(
                "Spend write failed; this response goes uncounted against the daily budget",
                exc_info=True,
            )
            # SpendLimits wants a total for every key; these feed only its event.
            return {entry.key: Spent() for entry in entries}


def build_spend_limits(
    redis: AioRedis, *, cap_usd: float, clock: Callable[[], datetime]
) -> SpendLimits[Any]:
    """The daily budget every agent in this process carries.

    The check before a request and the increment after it are separate round trips, so
    requests in flight at once can each pass the check and overshoot the cap by their
    own cost: one System 2 worker plus the Librarian, a few cents. Accepted (spec 7,
    "Accepted bound"); a reservation ledger would close it.
    """
    return SpendLimits(
        budgets=[
            Budget(
                usd=Decimal(str(cap_usd)), window="day", warn_at=ALERT_FRACTION, name=BUDGET_NAME
            )
        ],
        store=TolerantSpendStore(RedisSpendStore(redis, prefix=SPEND_PREFIX)),
        price=price,
        on_unpriced="zero",
        clock=clock,
    )


def local_clock(
    tz_name: Callable[[], str], *, utc_now: Callable[[], datetime] | None = None
) -> Callable[[], datetime]:
    """A clock in the zone *tz_name* names when it is read, so the budget day rolls over
    at the user's midnight. *utc_now* replaces the system clock in tests."""
    now = utc_now or (lambda: datetime.now(UTC))
    return lambda: now().astimezone(ZoneInfo(tz_name()))


async def _daily(limits: SpendLimits[Any]) -> tuple[BudgetStatus, str]:
    """Today's status and its day, read from the store key the status was taken under."""
    status = next(s for s in await limits.status() if s.budget.name == BUDGET_NAME)
    # The key is `name|window|scope|bucket` and the bucket is the last field.
    return status, status.key.rsplit("|", 1)[-1]


async def spend_snapshot(limits: SpendLimits[Any], redis: AioRedis) -> dict[str, Any]:
    """Today's spend in the admin overview's ``cost`` shape."""
    status, day = await _daily(limits)
    spend = float(status.spent.usd)
    count = status.spent.requests
    return {
        "date": day,
        "spend_usd": spend,
        "cap_usd": float(status.budget.usd or 0),
        "alert_sent": bool(await redis.exists(SPEND_ALERT_KEY.format(day))),
        "request_count": count,
        "avg_usd": round(spend / count, 6) if count else 0.0,
    }


async def alert_if_needed(
    limits: SpendLimits[Any],
    redis: AioRedis,
    send: Callable[[str, str], Awaitable[object]] | None,
) -> bool:
    """Send today's 80% alert if spend has reached it and no process has sent it yet.

    *send* takes a title and a body; core passes ``NotificationPublisher.urgent``'s
    sender (``shared`` imports nothing from core). Without one the marker is left
    unclaimed, for a process that has one. A failed send releases the marker, so the
    next call can try again.
    """
    if send is None:
        return False
    status, day = await _daily(limits)
    if not status.warning:
        return False
    key = SPEND_ALERT_KEY.format(day)
    if not await redis.set(key, "1", nx=True, ex=_ALERT_TTL_S):
        return False
    try:
        await send(
            "Budget Warning",
            f"Daily spend ${status.spent.usd:.2f} has reached 80% of "
            f"${status.budget.usd or 0:.2f} cap",
        )
    except Exception:
        await redis.delete(key)
        raise
    return True
