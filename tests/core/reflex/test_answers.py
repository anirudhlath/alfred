"""The open ask, and how an ask ends: yes, no, or silence."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from loguru import logger

from core.reflex.answers import (
    ASK_REPLY_CHANNELS,
    AnswerKind,
    OpenAsk,
    ReflexAnswer,
    read_open_ask,
    record_answer,
    settle_expired,
)
from core.reflex.settings import MAX_LIST_ITEMS, ReflexSettings, load_settings, save_settings
from shared.streams import (
    REFLEX_ANSWERED_PREFIX,
    REFLEX_ANSWERS_MAXLEN,
    REFLEX_ANSWERS_STREAM,
    REFLEX_ASK_OPEN_KEY,
    REFLEX_SETTINGS_KEY,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def an_ask(deadline: datetime, ask_id: str = "ask-1", *, script: bool = False) -> OpenAsk:
    return OpenAsk(
        ask_id=ask_id,
        tool_name="home.script_run" if script else "home.light_turn_on",
        targets=["movie_night"] if script else ["light.lamp"],
        origin="trigger_fired",
        asked_at=deadline - timedelta(seconds=600),
        deadline=deadline,
        noticed="Dusk",
        script=script,
    )


async def answers(redis: fakeredis.FakeAsyncRedis) -> list[ReflexAnswer]:
    return [
        ReflexAnswer.model_validate_json(fields[b"event"])
        for _id, fields in await redis.xrange(REFLEX_ANSWERS_STREAM)
    ]


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


async def test_a_settled_ask_is_recorded_as_expired_at_its_deadline(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    ask = an_ask(NOW - timedelta(minutes=3))
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())

    await settle_expired(redis, now=NOW)

    (answer,) = await answers(redis)
    assert answer == ReflexAnswer(
        ask_id="ask-1",
        answer="expired",
        time=ask.deadline,
        channel=None,
        tool_name="home.light_turn_on",
        targets=["light.lamp"],
        origin="trigger_fired",
        noticed="Dusk",
    )


async def test_settling_twice_records_one_expiry(redis: fakeredis.FakeAsyncRedis) -> None:
    ask = an_ask(NOW - timedelta(minutes=3))
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())

    await settle_expired(redis, now=NOW)
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())  # a redelivery of the same ask
    await settle_expired(redis, now=NOW + timedelta(minutes=1))

    assert len(await answers(redis)) == 1


async def test_an_ask_already_answered_is_still_cleared_when_it_expires(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """A crash after a yes's marker but before its delete leaves the key behind: without the
    settle's own delete it would hold every later ask for the rest of its day."""
    ask = an_ask(NOW - timedelta(minutes=3))
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())
    await redis.set(f"{REFLEX_ANSWERED_PREFIX}ask-1", "1", ex=60)

    assert await settle_expired(redis, now=NOW) == ask

    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)
    assert await answers(redis) == []


async def test_an_expired_script_ask_allows_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(REFLEX_ASK_OPEN_KEY, an_ask(NOW, script=True).model_dump_json())

    await settle_expired(redis, now=NOW)

    assert (await load_settings(redis)).allowed_scripts == []


# --- recording an answer ------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["yes", "no", "expired"])
async def test_an_answer_is_recorded_once_with_its_ask(
    redis: fakeredis.FakeAsyncRedis, kind: AnswerKind
) -> None:
    ask = an_ask(NOW + timedelta(minutes=5))
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())

    assert await record_answer(redis, ask, kind, at=NOW, channel="door") is True

    (answer,) = await answers(redis)
    assert answer == ReflexAnswer(
        ask_id="ask-1",
        answer=kind,
        time=NOW,
        channel="door",
        tool_name="home.light_turn_on",
        targets=["light.lamp"],
        origin="trigger_fired",
        noticed="Dusk",
    )
    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_the_marker_lasts_two_days(redis: fakeredis.FakeAsyncRedis) -> None:
    await record_answer(redis, an_ask(NOW), "no", at=NOW, channel="web_pwa")

    ttl = await redis.ttl(f"{REFLEX_ANSWERED_PREFIX}ask-1")

    assert 2 * 86_400 - 60 < ttl <= 2 * 86_400


async def test_a_second_answer_to_the_same_ask_changes_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    ask = an_ask(NOW + timedelta(minutes=5), script=True)
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())
    assert await record_answer(redis, ask, "no", at=NOW, channel="door") is True

    assert await record_answer(redis, ask, "yes", at=NOW, channel="web_pwa") is False

    (answer,) = await answers(redis)
    assert (answer.answer, answer.channel) == ("no", "door")
    assert (await load_settings(redis)).allowed_scripts == []


async def test_answers_join_the_stream_capped_and_approximate(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    real_xadd = redis.xadd

    async def spy(*args: object, **kwargs: object) -> object:
        return await real_xadd(*args, **kwargs)  # type: ignore[arg-type]

    xadd = AsyncMock(side_effect=spy)

    with patch.object(redis, "xadd", xadd):
        await record_answer(redis, an_ask(NOW), "yes", at=NOW, channel="door")

    xadd.assert_awaited_once()
    assert len(await answers(redis)) == 1
    assert xadd.await_args.args[0] == REFLEX_ANSWERS_STREAM
    assert xadd.await_args.kwargs == {"maxlen": REFLEX_ANSWERS_MAXLEN, "approximate": True}
    assert REFLEX_ANSWERS_MAXLEN == 5_000


async def test_an_answer_leaves_the_key_of_a_newer_ask(redis: fakeredis.FakeAsyncRedis) -> None:
    newer = an_ask(NOW + timedelta(minutes=10), "ask-2")
    await redis.set(REFLEX_ASK_OPEN_KEY, newer.model_dump_json())

    await record_answer(redis, an_ask(NOW, "ask-1"), "yes", at=NOW, channel="door")

    assert await read_open_ask(redis) == newer


async def test_the_open_key_is_matched_by_the_ask_it_names_not_by_its_bytes(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Another writer's serialisation (spacing, key order) is still this ask."""
    ask = an_ask(NOW + timedelta(minutes=5))
    reordered = dict(reversed(list(ask.model_dump(mode="json").items())))
    await redis.set(REFLEX_ASK_OPEN_KEY, json.dumps(reordered, indent=2))

    await record_answer(redis, ask, "no", at=NOW, channel="door")

    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_an_unreadable_open_key_is_left_alone_by_an_answer(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(REFLEX_ASK_OPEN_KEY, "{not json")

    assert await record_answer(redis, an_ask(NOW), "no", at=NOW, channel="door") is True

    assert await redis.get(REFLEX_ASK_OPEN_KEY) == b"{not json"


# --- a yes to a script --------------------------------------------------------------------


async def test_a_yes_to_a_script_allows_its_targets(redis: fakeredis.FakeAsyncRedis) -> None:
    await save_settings(redis, ReflexSettings(allowed_scripts=["bedtime"]))

    await record_answer(redis, an_ask(NOW, script=True), "yes", at=NOW, channel="door")

    assert (await load_settings(redis)).allowed_scripts == ["bedtime", "movie_night"]


@pytest.mark.parametrize("kind", ["no", "expired"])
async def test_a_no_or_silence_allows_nothing(
    redis: fakeredis.FakeAsyncRedis, kind: AnswerKind
) -> None:
    await record_answer(redis, an_ask(NOW, script=True), kind, at=NOW, channel=None)

    assert await redis.get(REFLEX_SETTINGS_KEY) is None


async def test_a_yes_to_something_that_is_not_a_script_allows_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await record_answer(redis, an_ask(NOW), "yes", at=NOW, channel="door")

    assert await redis.get(REFLEX_SETTINGS_KEY) is None


async def test_a_script_already_allowed_is_not_added_again(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await save_settings(redis, ReflexSettings(allowed_scripts=["movie_night"], daily_ask_cap=3))

    await record_answer(redis, an_ask(NOW, script=True), "yes", at=NOW, channel="door")

    settings = await load_settings(redis)
    assert settings.allowed_scripts == ["movie_night"]
    assert settings.daily_ask_cap == 3


async def test_a_full_allow_list_refuses_the_script_but_the_answer_stands(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    full = [f"script_{n}" for n in range(MAX_LIST_ITEMS)]
    await save_settings(redis, ReflexSettings(allowed_scripts=full))
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        recorded = await record_answer(
            redis, an_ask(NOW, script=True), "yes", at=NOW, channel="door"
        )
    finally:
        logger.remove(sink)

    assert recorded is True
    assert [a.answer for a in await answers(redis)] == ["yes"]
    assert (await load_settings(redis)).allowed_scripts == full
    assert any("did not allow" in line for line in warnings)


async def test_an_unusable_settings_document_is_not_written_over(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    broken = json.dumps({"mode": "ask", "ask_tool": ["home.*"]})  # an unknown field
    await redis.set(REFLEX_SETTINGS_KEY, broken)

    assert await record_answer(redis, an_ask(NOW, script=True), "yes", at=NOW, channel="door")

    assert await redis.get(REFLEX_SETTINGS_KEY) == broken.encode()


# --- reading the open ask -----------------------------------------------------------------


async def test_the_open_ask_is_read_without_being_touched(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    assert await read_open_ask(redis) is None
    ask = an_ask(NOW - timedelta(hours=1))  # long past its deadline: reading does not settle it
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())

    assert await read_open_ask(redis) == ask
    assert await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_an_unreadable_open_ask_reads_as_none_and_stays(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(REFLEX_ASK_OPEN_KEY, "{not json")

    assert await read_open_ask(redis) is None
    assert await redis.exists(REFLEX_ASK_OPEN_KEY)


def test_a_reply_answers_an_ask_on_the_channels_its_notification_reaches() -> None:
    assert frozenset({"web_pwa", "ios", "signal"}) == ASK_REPLY_CHANNELS
