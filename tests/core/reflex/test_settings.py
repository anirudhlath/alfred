"""Reflex's settings document: defaults for anything missing or bad, one warning per bad value."""

from __future__ import annotations

from typing import TYPE_CHECKING

import fakeredis
import pytest
from loguru import logger

from core.reflex import settings as settings_module
from core.reflex.settings import ReflexSettings, load_settings, save_settings
from shared.streams import REFLEX_SETTINGS_KEY

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module remembers the bad values it has warned about; each test starts with none."""
    monkeypatch.setattr(settings_module, "_warned", set())


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="WARNING", format="{message}")
    yield messages
    logger.remove(sink_id)


async def test_a_missing_document_reads_as_the_defaults(warnings: list[str]) -> None:
    redis = fakeredis.FakeAsyncRedis()

    settings = await load_settings(redis)

    assert settings == ReflexSettings()
    assert settings.reverse_window_minutes == 60
    assert warnings == []


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        '{"reverse_window_minutes": 0}',
        '{"reverse_window_minutes": 1441}',
        '{"reverse_window_minutes": "x"}',
        '{"surprise": 1}',
        "[]",
        b"\xff\xfe",
    ],
)
async def test_a_bad_document_reads_as_the_defaults(raw: str | bytes) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, raw)

    assert await load_settings(redis) == ReflexSettings()


async def test_a_bad_value_logs_once_over_two_reads(warnings: list[str]) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, "{")

    await load_settings(redis)
    await load_settings(redis)

    assert len(warnings) == 1


async def test_a_different_bad_value_logs_again(warnings: list[str]) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, "{")
    await load_settings(redis)

    await redis.set(REFLEX_SETTINGS_KEY, '{"reverse_window_minutes": 0}')
    await load_settings(redis)
    await load_settings(redis)

    assert len(warnings) == 2


async def test_saving_then_loading_round_trips() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await save_settings(redis, ReflexSettings(reverse_window_minutes=15))

    assert await load_settings(redis) == ReflexSettings(reverse_window_minutes=15)
