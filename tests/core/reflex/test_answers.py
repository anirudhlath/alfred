"""The open ask and how an unanswered one ends."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from loguru import logger

from core.reflex.answers import OpenAsk, settle_expired
from shared.streams import REFLEX_ASK_OPEN_KEY

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def an_ask(deadline: datetime, ask_id: str = "ask-1") -> OpenAsk:
    return OpenAsk(
        ask_id=ask_id,
        tool_name="home.light_turn_on",
        targets=["light.lamp"],
        origin="trigger_fired",
        asked_at=deadline - timedelta(seconds=600),
        deadline=deadline,
        noticed="Dusk",
        script=False,
    )


def test_importing_answers_pulls_in_nothing_from_routing() -> None:
    """``core/routing/pending.py`` imports ``answers``, so ``answers`` may not import it back."""
    code = (
        "import sys, core.reflex.answers;"
        " assert not [m for m in sys.modules if m.startswith('core.routing')]"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_an_open_ask_reads_a_time_with_no_offset_as_utc() -> None:
    ask = OpenAsk.model_validate(
        {
            **an_ask(NOW).model_dump(mode="json"),
            "asked_at": "2026-10-28T11:50:00",
            "deadline": "2026-10-28T12:00:00",
        }
    )

    assert ask.asked_at == NOW - timedelta(minutes=10)
    assert ask.deadline == NOW


async def test_no_open_ask_settles_nothing(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await settle_expired(redis, now=NOW) is None


async def test_an_ask_still_waiting_is_left_open(redis: fakeredis.FakeAsyncRedis) -> None:
    ask = an_ask(NOW + timedelta(seconds=1))
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())

    assert await settle_expired(redis, now=NOW) is None
    assert await redis.exists(REFLEX_ASK_OPEN_KEY)


@pytest.mark.parametrize("late", [timedelta(0), timedelta(seconds=1), timedelta(hours=3)])
async def test_an_ask_past_its_deadline_is_settled_and_returned(
    redis: fakeredis.FakeAsyncRedis, late: timedelta
) -> None:
    ask = an_ask(NOW - late)
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())

    assert await settle_expired(redis, now=NOW) == ask
    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_an_unreadable_open_value_is_deleted_with_a_warning(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(REFLEX_ASK_OPEN_KEY, "{not json")
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        result = await settle_expired(redis, now=NOW)
    finally:
        logger.remove(sink)

    assert result is None
    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)
    assert any("unreadable open ask" in line for line in warnings)


async def test_the_delete_leaves_a_newer_ask_that_took_the_key_meanwhile(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    expired = an_ask(NOW - timedelta(minutes=1), "old")
    newer = an_ask(NOW + timedelta(minutes=10), "new")
    await redis.set(REFLEX_ASK_OPEN_KEY, newer.model_dump_json())

    # The read saw the expired ask; by the time of the delete, the next ask had claimed the key.
    with patch.object(redis, "get", AsyncMock(return_value=expired.model_dump_json().encode())):
        settled = await settle_expired(redis, now=NOW)

    assert settled == expired
    stored = await redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored) == newer
