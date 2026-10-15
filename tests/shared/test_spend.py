"""Spend on the harness's SpendLimits: pricing, the shared counter, the alert, the snapshot."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import fakeredis
import pytest
from genai_prices import Usage, calc_price
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import RedisSpendStore, SpendEntry, SpendLimitExceeded, Spent

from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Urgency
from shared import spend
from shared.spend import (
    ALERT_FRACTION,
    ALERT_SOURCE,
    FALLBACK_INPUT_USD_PER_MTOK,
    FALLBACK_OUTPUT_USD_PER_MTOK,
    TolerantSpendStore,
    alert_if_needed,
    build_spend_limits,
    local_clock,
    price,
    spend_snapshot,
)
from shared.streams import SPEND_ALERT_KEY, SPEND_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic_ai_harness.spend import SpendLimits

_NOON = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _fixed(at: datetime) -> Callable[[], datetime]:
    return lambda: at


def _response(model: str | None, provider: str | None, *, inp: int, out: int) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart("ok")],
        usage=RequestUsage(input_tokens=inp, output_tokens=out),
        model_name=model,
        provider_name=provider,
    )


@pytest.fixture(autouse=True)
def _fresh_unpriced_warnings() -> None:
    spend._warn_unpriced.cache_clear()


# --- price ------------------------------------------------------------------------------


def test_a_vllm_response_is_free() -> None:
    assert price(_response("served-model", "vllm", inp=10_000, out=10_000)) == Decimal(0)


def test_a_known_openrouter_model_is_priced_by_genai_prices() -> None:
    response = _response("anthropic/claude-sonnet-4", "openrouter", inp=1_000, out=1_000)

    expected = calc_price(
        Usage(input_tokens=1_000, output_tokens=1_000),
        model_ref="anthropic/claude-sonnet-4",
        provider_id="openrouter",
    ).total_price
    assert price(response) == response.cost().total_price == expected


def test_an_unknown_paid_model_is_priced_at_the_fallback_rates_and_warned_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = _response("nobody/unlisted-model", "openrouter", inp=2_000_000, out=1_000_000)

    with caplog.at_level(logging.WARNING, logger="shared.spend"):
        first = price(response)
        second = price(response)

    expected = 2 * FALLBACK_INPUT_USD_PER_MTOK + FALLBACK_OUTPUT_USD_PER_MTOK
    assert first == second == expected == Decimal("21")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "nobody/unlisted-model" in warnings[0].getMessage()


# --- the shared counter (#309) ----------------------------------------------------------


def _billing_agent(limits: SpendLimits[Any]) -> Agent[None, str]:
    """An agent whose every response costs the same: 1000 input and 100 output tokens."""

    def answer(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart("ok")], usage=RequestUsage(input_tokens=1_000, output_tokens=100)
        )

    return Agent(FunctionModel(answer), capabilities=[limits])


@pytest.mark.parametrize("cap", [0.0, -1.0])
async def test_a_cap_of_zero_or_less_refuses_every_request(cap: float) -> None:
    """``DAILY_COST_CAP_USD`` <= 0 is the kill switch: nothing paid runs, nothing alerts."""
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=cap, clock=_fixed(_NOON))
    calls = 0

    async def answer(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("never")])

    assert await limits.exhausted()
    with pytest.raises(SpendLimitExceeded):
        await Agent(FunctionModel(answer), capabilities=[limits]).run("hello")
    assert calls == 0
    snapshot = await spend_snapshot(limits, redis)
    assert (snapshot["spend_usd"], snapshot["cap_usd"]) == (0.0, 0.0)
    assert await alert_if_needed(limits, redis, AsyncMock()) is False


async def test_two_processes_accruing_concurrently_lose_no_update() -> None:
    server = fakeredis.FakeServer()
    first = build_spend_limits(
        fakeredis.FakeAsyncRedis(server=server), cap_usd=5.0, clock=_fixed(_NOON)
    )
    second = build_spend_limits(
        fakeredis.FakeAsyncRedis(server=server), cap_usd=5.0, clock=_fixed(_NOON)
    )
    agents = [_billing_agent(first), _billing_agent(second)]

    await asyncio.gather(*(agent.run("go") for agent in agents for _ in range(50)))

    per_response = (
        1_000 * FALLBACK_INPUT_USD_PER_MTOK + 100 * FALLBACK_OUTPUT_USD_PER_MTOK
    ) / 1_000_000
    for limits in (first, second):
        (status,) = await limits.status()
        assert status.spent.requests == 100
        assert status.spent.tokens == 100 * 1_100
        assert status.spent.usd == 100 * per_response


async def test_the_counter_lives_under_the_spend_prefix() -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=5.0, clock=_fixed(_NOON))

    await _billing_agent(limits).run("go")

    keys = {k.decode() for k in await redis.keys("*")}
    # The counter, plus the store's replay marker for the response.
    assert f"{{{SPEND_PREFIX}}}:daily|day|*|2026-10-14" in keys
    assert all(k.startswith(f"{{{SPEND_PREFIX}}}:") for k in keys)


# --- the alert --------------------------------------------------------------------------


async def _spent(limits: SpendLimits[Any], usd: str) -> None:
    (status,) = await limits.status()
    await limits.store.add_many(  # type: ignore[union-attr]
        [SpendEntry(key=status.key, usd=Decimal(usd), requests=1, ttl=timedelta(hours=48))]
    )


async def test_two_processes_at_85_percent_send_exactly_one_alert() -> None:
    server = fakeredis.FakeServer()
    r1 = fakeredis.FakeAsyncRedis(server=server)
    r2 = fakeredis.FakeAsyncRedis(server=server)
    first = build_spend_limits(r1, cap_usd=10.0, clock=_fixed(_NOON))
    second = build_spend_limits(r2, cap_usd=10.0, clock=_fixed(_NOON))
    await _spent(first, "8.5")
    d1, d2 = AsyncMock(), AsyncMock()
    s1 = NotificationPublisher(d1).urgent(ALERT_SOURCE)
    s2 = NotificationPublisher(d2).urgent(ALERT_SOURCE)

    sent = await asyncio.gather(alert_if_needed(first, r1, s1), alert_if_needed(second, r2, s2))

    assert sorted(sent) == [False, True]
    assert d1.dispatch.await_count + d2.dispatch.await_count == 1
    dispatch = d1.dispatch if d1.dispatch.await_count else d2.dispatch
    (notification,) = dispatch.await_args.args
    assert notification.title == "Budget Warning"
    assert notification.body == "Daily spend $8.50 has reached 80% of $10.00 cap"
    assert notification.source == "spend"
    assert notification.urgency is Urgency.URGENT
    assert await r1.ttl(SPEND_ALERT_KEY.format("2026-10-14")) == 172800
    # A later turn the same day sends nothing.
    assert await alert_if_needed(first, r1, s1) is False


async def test_below_the_alert_fraction_nothing_is_sent_or_claimed() -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=10.0, clock=_fixed(_NOON))
    await _spent(limits, "7.99")
    send = AsyncMock()

    assert ALERT_FRACTION == 0.8
    assert await alert_if_needed(limits, redis, send) is False
    send.assert_not_awaited()
    assert not await redis.exists(SPEND_ALERT_KEY.format("2026-10-14"))


async def test_without_a_notifier_the_alert_is_left_for_a_process_that_has_one() -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=10.0, clock=_fixed(_NOON))
    await _spent(limits, "9")

    assert await alert_if_needed(limits, redis, None) is False
    assert not await redis.exists(SPEND_ALERT_KEY.format("2026-10-14"))


async def test_a_failed_publish_releases_the_days_alert() -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=10.0, clock=_fixed(_NOON))
    await _spent(limits, "9")
    send = AsyncMock(side_effect=ConnectionError("dispatch down"))

    with pytest.raises(ConnectionError):
        await alert_if_needed(limits, redis, send)

    assert not await redis.exists(SPEND_ALERT_KEY.format("2026-10-14"))


# --- local midnight ---------------------------------------------------------------------


async def test_the_day_rolls_over_at_local_midnight() -> None:
    redis = fakeredis.FakeAsyncRedis()
    # 05:30 and 06:30 UTC on one UTC day are 23:30 and 00:30 in Denver (UTC-6 in October).
    instant = [datetime(2026, 10, 14, 5, 30, tzinfo=UTC)]
    clock = local_clock(lambda: "America/Denver", utc_now=lambda: instant[0])
    limits = build_spend_limits(redis, cap_usd=10.0, clock=clock)

    assert clock().hour == 23
    await _spent(limits, "4")
    before = await spend_snapshot(limits, redis)
    instant[0] = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
    after = await spend_snapshot(limits, redis)

    assert clock().hour == 0
    assert (before["date"], before["spend_usd"]) == ("2026-10-13", 4.0)
    assert (after["date"], after["spend_usd"]) == ("2026-10-14", 0.0)


def test_the_local_clock_follows_the_zone_it_is_given() -> None:
    zone = ["UTC"]
    clock = local_clock(lambda: zone[0], utc_now=_fixed(_NOON))

    assert clock().hour == 12
    zone[0] = "Asia/Kolkata"
    assert (clock().hour, clock().minute) == (17, 30)


# --- the snapshot -----------------------------------------------------------------------


async def test_the_snapshot_keeps_the_admin_cost_shape() -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=5.0, clock=_fixed(_NOON))
    await _spent(limits, "1.5")
    await _spent(limits, "0.5")

    snapshot = await spend_snapshot(limits, redis)

    assert snapshot == {
        "date": "2026-10-14",
        "spend_usd": 2.0,
        "cap_usd": 5.0,
        "alert_sent": False,
        "request_count": 2,
        "avg_usd": 1.0,
    }
    await redis.set(SPEND_ALERT_KEY.format("2026-10-14"), "1")
    assert (await spend_snapshot(limits, redis))["alert_sent"] is True


async def test_an_empty_day_snapshots_as_zero() -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=5.0, clock=_fixed(_NOON))

    snapshot = await spend_snapshot(limits, redis)

    assert (snapshot["spend_usd"], snapshot["request_count"], snapshot["avg_usd"]) == (0.0, 0, 0.0)


# --- write tolerance --------------------------------------------------------------------


class _Store:
    """A batch store whose writes or reads can be made to fail."""

    def __init__(self, *, fail_writes: bool = False, fail_reads: bool = False) -> None:
        self.fail_writes = fail_writes
        self.fail_reads = fail_reads

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Spent]:
        if self.fail_reads:
            raise ConnectionError("redis down")
        return {key: Spent() for key in keys}

    async def add_many(self, entries: Sequence[SpendEntry]) -> Mapping[str, Spent]:
        if self.fail_writes:
            raise ConnectionError("redis down")
        return {entry.key: Spent(usd=entry.usd) for entry in entries}


async def test_a_failed_write_logs_and_returns_a_total_for_every_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = TolerantSpendStore(_Store(fail_writes=True))

    with caplog.at_level(logging.WARNING, logger="shared.spend"):
        totals = await store.add_many(
            [SpendEntry(key="a", usd=Decimal(1)), SpendEntry(key="b", usd=Decimal(2))]
        )

    assert totals == {"a": Spent(), "b": Spent()}
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_a_failed_read_still_raises() -> None:
    store = TolerantSpendStore(_Store(fail_reads=True))

    with pytest.raises(ConnectionError):
        await store.get_many(["a"])


async def test_a_turn_whose_spend_write_fails_still_answers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    limits = build_spend_limits(redis, cap_usd=5.0, clock=_fixed(_NOON))
    inner = limits.store.inner  # type: ignore[union-attr]
    assert isinstance(inner, RedisSpendStore)
    inner.add_many = AsyncMock(side_effect=ConnectionError("closed"))  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="shared.spend"):
        result = await _billing_agent(limits).run("go")

    assert result.output == "ok"
    assert any("Spend write failed" in r.getMessage() for r in caplog.records)
