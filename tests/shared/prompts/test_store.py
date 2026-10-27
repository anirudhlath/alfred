"""PromptStore: Langfuse first, then the Redis copy, then the repo default."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from shared.prompts.langfuse import LangfuseError
from shared.prompts.names import SYSTEM2_LOCATION, SYSTEM2_PERSONALITY
from shared.prompts.store import PromptStore
from shared.prompts.template import repo_text
from shared.streams import PROMPT_COPY_KEY
from tests.shared.prompts.fakes import FakeLangfuse


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _store(fake: FakeLangfuse, **kwargs: object) -> tuple[PromptStore, Clock]:
    clock = Clock()
    return PromptStore(langfuse=fake, clock=clock, **kwargs), clock  # type: ignore[arg-type]


async def _settle(store: PromptStore) -> None:
    """Wait for the store's in-flight refreshes (on the in-memory fake) to finish."""
    while store._inflight:
        await asyncio.gather(*store._inflight.values(), return_exceptions=True)
        await asyncio.sleep(0)  # let the done-callbacks drop the finished tasks


async def _started() -> None:
    """Give freshly created reader tasks their first step (they queue on the gate)."""
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_without_langfuse_the_repo_default_is_served() -> None:
    prompt = await PromptStore().get(SYSTEM2_LOCATION, area="hall")
    assert (prompt.source, prompt.version) == ("repo", None)
    assert "hall satellite" in prompt.text


@pytest.mark.asyncio
async def test_langfuse_text_is_rendered_at_the_stores_label() -> None:
    fake = FakeLangfuse({SYSTEM2_LOCATION: "In the {{area}}."})
    store, _ = _store(fake, label="candidate")

    prompt = await store.get(SYSTEM2_LOCATION, area="hall")

    assert (prompt.text, prompt.version, prompt.source) == ("In the hall.", 7, "langfuse")
    assert fake.calls == [(SYSTEM2_LOCATION, "candidate")]


@pytest.mark.asyncio
async def test_reads_within_the_ttl_are_served_from_memory() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "You are Alfred."})
    store, clock = _store(fake)

    await store.get(SYSTEM2_PERSONALITY)
    clock.now += 59
    await store.get(SYSTEM2_PERSONALITY)

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_stale_read_serves_the_cached_text_and_refreshes_in_the_background() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "v1"})
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)

    fake.texts[SYSTEM2_PERSONALITY] = "v2"
    fake.gate = asyncio.Event()
    clock.now += 61
    stale = await asyncio.wait_for(store.get(SYSTEM2_PERSONALITY), timeout=0.5)
    assert stale.text == "v1"

    fake.gate.set()
    await _settle(store)
    assert (await store.get(SYSTEM2_PERSONALITY)).text == "v2"
    await store.aclose()


@pytest.mark.asyncio
async def test_an_unseeded_prompt_serves_the_repo_default_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, _ = _store(FakeLangfuse())
    with caplog.at_level(logging.INFO, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_PERSONALITY)
    assert (prompt.text, prompt.source) == (repo_text(SYSTEM2_PERSONALITY), "repo")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_langfuse_errors_fall_back_to_the_redis_copy() -> None:
    redis = fakeredis.FakeAsyncRedis()
    first, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "From Langfuse."}), redis=redis)
    await first.get(SYSTEM2_PERSONALITY)  # writes the copy

    down = FakeLangfuse()
    down.error = LangfuseError("refused")
    second, _ = _store(down, redis=redis)
    prompt = await second.get(SYSTEM2_PERSONALITY)

    assert (prompt.text, prompt.version, prompt.source) == ("From Langfuse.", 7, "redis")
    raw = await redis.get(PROMPT_COPY_KEY.format(name=SYSTEM2_PERSONALITY, label="production"))
    assert json.loads(raw) == {"text": "From Langfuse.", "version": 7}


@pytest.mark.asyncio
async def test_an_outage_is_cached_so_turns_do_not_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLangfuse()
    fake.error = LangfuseError("timed out")
    store, clock = _store(fake)

    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        first = await store.get(SYSTEM2_PERSONALITY)
        await store.get(SYSTEM2_PERSONALITY)
        clock.now += 61
        await store.get(SYSTEM2_PERSONALITY)
        await _settle(store)

    assert first.source == "repo"
    assert len(fake.calls) == 2  # the cold read and one background refresh, not one per read
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    await store.aclose()


@pytest.mark.asyncio
async def test_a_langfuse_text_with_an_undeclared_variable_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, _ = _store(FakeLangfuse({SYSTEM2_LOCATION: "In the {{room}}."}))
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_LOCATION, area="hall")
    assert prompt.source == "repo"
    assert any("room" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_chat_prompt_is_refused(caplog: pytest.LogCaptureFixture) -> None:
    store, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "x"}, prompt_type="chat"))
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_PERSONALITY)
    assert prompt.source == "repo"
    assert any("not a text prompt" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_concurrent_cold_reads_share_one_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "Shared."})
    fake.gate = asyncio.Event()
    store, _ = _store(fake)

    readers = [asyncio.create_task(store.get(SYSTEM2_PERSONALITY)) for _ in range(5)]
    await _started()
    fake.gate.set()
    results = await asyncio.gather(*readers)

    assert {r.text for r in results} == {"Shared."}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_cancelled_reader_does_not_cancel_the_shared_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "Shared."})
    fake.gate = asyncio.Event()
    store, _ = _store(fake)

    quitter = asyncio.create_task(store.get(SYSTEM2_PERSONALITY))
    stayer = asyncio.create_task(store.get(SYSTEM2_PERSONALITY))
    await _started()
    quitter.cancel()
    fake.gate.set()

    assert (await stayer).text == "Shared."
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_redis_failures_never_reach_the_caller() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RedisConnectionError("down"))
    redis.set = AsyncMock(side_effect=RedisConnectionError("down"))

    up, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "Up."}), redis=redis)
    assert (await up.get(SYSTEM2_PERSONALITY)).text == "Up."

    down = FakeLangfuse()
    down.error = LangfuseError("refused")
    both_down, _ = _store(down, redis=redis)
    assert (await both_down.get(SYSTEM2_PERSONALITY)).source == "repo"


@pytest.mark.asyncio
async def test_wrong_variables_are_a_value_error_before_any_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_LOCATION: "x"})
    store, _ = _store(fake)
    with pytest.raises(ValueError, match=SYSTEM2_LOCATION):
        await store.get(SYSTEM2_LOCATION)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_aclose_cancels_refreshes_and_closes_the_client() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "v1"})
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)
    fake.gate = asyncio.Event()
    clock.now += 61
    await store.get(SYSTEM2_PERSONALITY)  # starts a refresh that never finishes

    await asyncio.wait_for(store.aclose(), timeout=0.5)

    assert fake.closed


def test_describe_names_the_source() -> None:
    assert PromptStore().describe() == "repo defaults"
    store, _ = _store(FakeLangfuse(), label="candidate")
    assert store.describe() == "Langfuse, label candidate"


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_last_langfuse_text() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "Good."})
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)

    fake.error = LangfuseError("refused")
    clock.now += 61
    await store.get(SYSTEM2_PERSONALITY)  # serves the cache, starts the refresh
    await _settle(store)
    clock.now += 1
    prompt = await store.get(SYSTEM2_PERSONALITY)

    assert (prompt.text, prompt.version, prompt.source) == ("Good.", 7, "langfuse")
    assert len(fake.calls) == 2  # the failure re-stamped the entry: no refresh per read


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_a_last_good_text_even_with_redis_down() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RedisConnectionError("down"))
    redis.set = AsyncMock(side_effect=RedisConnectionError("down"))
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "Good."})
    store, clock = _store(fake, redis=redis)
    await store.get(SYSTEM2_PERSONALITY)

    fake.error = LangfuseError("refused")
    clock.now += 61
    await store.get(SYSTEM2_PERSONALITY)
    await _settle(store)

    assert (await store.get(SYSTEM2_PERSONALITY)).source == "langfuse"


class _Hang:
    """A Redis whose calls never answer."""

    def __init__(self) -> None:
        self.never = asyncio.Event()

    async def get(self, key: str) -> None:
        await self.never.wait()

    async def set(self, key: str, value: str) -> None:
        await self.never.wait()


@pytest.mark.asyncio
async def test_a_hung_redis_never_holds_a_read_for_long() -> None:
    down = FakeLangfuse()
    down.error = LangfuseError("refused")
    store, _ = _store(down, redis=_Hang())
    prompt = await asyncio.wait_for(store.get(SYSTEM2_PERSONALITY), timeout=1.5)
    assert prompt.source == "repo"

    up, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "Up."}), redis=_Hang())
    prompt = await asyncio.wait_for(up.get(SYSTEM2_PERSONALITY), timeout=1.5)
    assert (prompt.text, prompt.source) == ("Up.", "langfuse")


@pytest.mark.asyncio
async def test_an_unexpected_error_falls_back_instead_of_escaping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "x"})
    fake.error = ValueError("odd")
    store, _ = _store(fake)
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_PERSONALITY)
    assert prompt.source == "repo"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.asyncio
async def test_after_aclose_reads_are_served_without_a_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "v1"})
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)
    await store.aclose()

    clock.now += 61
    cached = await store.get(SYSTEM2_PERSONALITY)  # stale, but no refresh starts
    cold = await store.get(SYSTEM2_LOCATION, area="hall")  # never cached: the repo default

    assert (cached.text, cached.source) == ("v1", "langfuse")
    assert cold.source == "repo"
    assert len(fake.calls) == 1
    assert not store._inflight


@pytest.mark.asyncio
async def test_recovery_is_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "x"})
    fake.error = LangfuseError("refused")
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)

    fake.error = None
    clock.now += 61
    with caplog.at_level(logging.INFO, logger="shared.prompts.store"):
        await store.get(SYSTEM2_PERSONALITY)
        await _settle(store)

    assert any(
        r.levelno == logging.INFO and "reachable again" in r.getMessage() for r in caplog.records
    )


@pytest.mark.parametrize(
    "stored",
    [
        json.dumps({"text": "In the {{room}}.", "version": 3}),  # undeclared variable
        "not json",
        json.dumps({"text": 5, "version": 3}),
        json.dumps({"text": "Fine.", "version": "3"}),
        json.dumps({"text": "Fine."}),
        json.dumps(["Fine.", 3]),
    ],
)
@pytest.mark.asyncio
async def test_an_unusable_redis_copy_is_ignored(stored: str) -> None:
    redis = fakeredis.FakeAsyncRedis()
    key = PROMPT_COPY_KEY.format(name=SYSTEM2_LOCATION, label="production")
    await redis.set(key, stored)
    down = FakeLangfuse()
    down.error = LangfuseError("refused")
    store, _ = _store(down, redis=redis)

    prompt = await store.get(SYSTEM2_LOCATION, area="hall")

    assert prompt.source == "repo"
