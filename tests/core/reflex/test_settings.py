"""Reflex's settings document: defaults for anything missing or bad, one warning per bad value."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import fakeredis
import pytest
from loguru import logger

from core.reflex import settings as settings_module
from core.reflex.settings import (
    ReflexSettings,
    execute,
    load_settings,
    parse_args,
    save_settings,
)
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


def test_the_defaults_are_the_documented_ones() -> None:
    assert ReflexSettings().model_dump() == {
        "mode": "shadow",
        "ask_tools": [],
        "allowed_scripts": [],
        "reverse_window_minutes": 60,
        "ask_expiry_seconds": 600,
        "repeat_hours": 2.0,
        "daily_ask_cap": 6,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "07:00",
    }


_FULL = {
    "mode": "ask",
    "ask_tools": ["alpha_*", "beta_off"],
    "allowed_scripts": ["target.one"],
    "reverse_window_minutes": 15,
    "ask_expiry_seconds": 3600,
    "repeat_hours": 0.5,
    "daily_ask_cap": 0,
    "quiet_hours_start": "22:30",
    "quiet_hours_end": "22:30",
}


async def test_a_full_document_round_trips_through_redis() -> None:
    redis = fakeredis.FakeAsyncRedis()
    settings = ReflexSettings.model_validate(_FULL)

    await save_settings(redis, settings)

    assert json.loads(await redis.get(REFLEX_SETTINGS_KEY)) == _FULL
    assert await load_settings(redis) == settings


async def test_a_partial_document_takes_the_defaults_for_the_rest() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, '{"mode": "ask"}')

    assert await load_settings(redis) == ReflexSettings(mode="ask")


@pytest.mark.parametrize(
    "document",
    [
        {"mode": "act"},
        {"mode": 5},
        {"ask_tools": "alpha_*"},
        {"ask_tools": ["ok", 1]},
        {"ask_tools": [""]},
        {"ask_tools": ["  "]},
        {"allowed_scripts": "target.x"},
        {"allowed_scripts": [None]},
        {"allowed_scripts": [""]},
        {"reverse_window_minutes": 1441},
        {"reverse_window_minutes": True},
        {"reverse_window_minutes": "60"},
        {"reverse_window_minutes": 60.0},
        {"ask_expiry_seconds": True},
        {"ask_expiry_seconds": "600"},
        {"repeat_hours": True},
        {"repeat_hours": "3"},
        {"daily_ask_cap": True},
        {"daily_ask_cap": "3"},
        {"ask_tools": ["a"] * 201},
        {"allowed_scripts": ["a"] * 201},
        {"ask_tools": ["a" * 257]},
        {"allowed_scripts": ["a" * 257]},
        {"ask_expiry_seconds": 59},
        {"ask_expiry_seconds": 3601},
        {"ask_expiry_seconds": "soon"},
        {"repeat_hours": -0.1},
        {"repeat_hours": 168.5},
        {"repeat_hours": "often"},
        {"daily_ask_cap": -1},
        {"daily_ask_cap": 51},
        {"daily_ask_cap": 2.5},
        {"quiet_hours_start": "24:00"},
        {"quiet_hours_start": "7:00"},
        {"quiet_hours_start": "23:60"},
        {"quiet_hours_start": "23:00\n"},
        {"quiet_hours_start": 2300},
        {"quiet_hours_end": "07:00pm"},
        {"quiet_hours_end": ""},
    ],
)
async def test_an_unusable_document_reads_as_the_defaults_with_one_warning(
    document: dict[str, object], warnings: list[str]
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, json.dumps(document))

    first = await load_settings(redis)
    await load_settings(redis)

    assert first == ReflexSettings()
    assert first.mode == "shadow"
    assert len(warnings) == 1


async def test_one_bad_field_discards_the_whole_document(warnings: list[str]) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, json.dumps({**_FULL, "quiet_hours_end": "7am"}))

    assert await load_settings(redis) == ReflexSettings()
    assert len(warnings) == 1


def test_a_whole_number_is_a_valid_repeat_interval() -> None:
    assert ReflexSettings.model_validate_json('{"repeat_hours": 2}').repeat_hours == 2.0
    assert ReflexSettings.model_validate({"repeat_hours": 0}).repeat_hours == 0.0


def test_the_lists_hold_200_items_of_256_characters() -> None:
    settings = ReflexSettings(ask_tools=["a" * 256] * 200, allowed_scripts=["b"] * 200)

    assert len(settings.ask_tools) == len(settings.allowed_scripts) == 200


@pytest.mark.parametrize(
    "document",
    [
        {"ask_expiry_seconds": 60},
        {"ask_expiry_seconds": 3600},
        {"repeat_hours": 0},
        {"repeat_hours": 168},
        {"daily_ask_cap": 0},
        {"daily_ask_cap": 50},
        {"quiet_hours_start": "00:00", "quiet_hours_end": "23:59"},
    ],
)
def test_the_bounds_are_inclusive(document: dict[str, object]) -> None:
    ReflexSettings.model_validate(document)


# --- the command line ---------------------------------------------------------------------


async def _run(redis: fakeredis.FakeAsyncRedis, *argv: str) -> int:
    return await execute(redis, parse_args(argv))


async def _stored(redis: fakeredis.FakeAsyncRedis) -> dict[str, object] | None:
    raw = await redis.get(REFLEX_SETTINGS_KEY)
    return None if raw is None else json.loads(raw)


async def test_show_prints_the_defaults_when_nothing_is_stored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "show") == 0

    assert json.loads(capsys.readouterr().out) == ReflexSettings().model_dump()
    assert await _stored(redis) is None


async def test_set_reads_a_bare_word_as_a_string_and_saves_the_whole_document() -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "set", "mode", "ask") == 0

    assert await _stored(redis) == ReflexSettings(mode="ask").model_dump()


async def test_set_reads_json_when_it_parses() -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "set", "daily_ask_cap", "3") == 0
    assert await _run(redis, "set", "repeat_hours", "0.5") == 0
    assert await _run(redis, "set", "repeat_hours", "2") == 0
    assert await _run(redis, "set", "repeat_hours", "0.5") == 0
    assert await _run(redis, "set", "ask_tools", '["alpha_*", "beta_off"]') == 0
    assert await _run(redis, "set", "quiet_hours_start", "22:30") == 0

    settings = await load_settings(redis)
    assert settings.daily_ask_cap == 3
    assert settings.repeat_hours == 0.5
    assert settings.ask_tools == ["alpha_*", "beta_off"]
    assert settings.quiet_hours_start == "22:30"


async def test_set_keeps_the_other_fields() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await save_settings(redis, ReflexSettings.model_validate(_FULL))

    assert await _run(redis, "set", "daily_ask_cap", "9") == 0

    assert await load_settings(redis) == ReflexSettings.model_validate(
        {**_FULL, "daily_ask_cap": 9}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "act"),
        ("daily_ask_cap", "51"),
        ("daily_ask_cap", "many"),
        ("daily_ask_cap", "true"),
        ("daily_ask_cap", '"3"'),
        ("reverse_window_minutes", "true"),
        ("repeat_hours", "true"),
        ("ask_tools", json.dumps(["a"] * 201)),
        ("allowed_scripts", json.dumps(["a" * 257])),
        ("redis", "1"),
        ("current", "1"),
        ("changes", "1"),
        ("quiet_hours_start", "7am"),
        ("quiet_hours_start", "2300"),
        ("ask_tools", "alpha_*"),
        ("allowed_scripts", "target.x"),
        ("surprise", "1"),
    ],
)
async def test_set_refuses_an_invalid_document_and_saves_nothing(
    field: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await save_settings(redis, ReflexSettings(mode="ask"))
    before = await redis.get(REFLEX_SETTINGS_KEY)

    assert await _run(redis, "set", field, value) != 0

    assert await redis.get(REFLEX_SETTINGS_KEY) == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert field in captured.err
    assert "nothing saved" in captured.err


_UNUSABLE_DOCUMENTS = [b"{not json", b'{"mode": "act"}', b'{"daily_ask_cap": true}']


@pytest.mark.parametrize("raw", _UNUSABLE_DOCUMENTS)
@pytest.mark.parametrize(
    "argv",
    [
        ("set", "mode", "ask"),
        ("allow-script", "target.a"),
        ("disallow-script", "target.a"),
    ],
)
async def test_a_command_never_edits_over_an_unusable_stored_document(
    raw: bytes, argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, raw)

    assert await _run(redis, *argv) != 0

    assert await redis.get(REFLEX_SETTINGS_KEY) == raw
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unusable" in captured.err
    assert "nothing saved" in captured.err
    assert "PUT /api/admin/reflex/settings" in captured.err


async def test_show_prints_the_defaults_and_says_the_stored_document_is_unusable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, b'{"mode": "act"}')

    assert await _run(redis, "show") == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == ReflexSettings().model_dump()
    assert "unusable" in captured.err
    assert "mode" in captured.err
    assert await redis.get(REFLEX_SETTINGS_KEY) == b'{"mode": "act"}'


async def test_a_missing_document_is_edited_from_the_defaults() -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "allow-script", "target.a") == 0

    assert await _stored(redis) == ReflexSettings(allowed_scripts=["target.a"]).model_dump()


def test_the_script_commands_take_an_id(capsys: pytest.CaptureFixture[str]) -> None:
    for command in ("allow-script", "disallow-script"):
        with pytest.raises(SystemExit):
            parse_args([command, "--help"])
        help_text = capsys.readouterr().out
        assert " id" in help_text
        assert "entity" not in help_text


async def test_allow_script_adds_in_order_and_is_idempotent() -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "allow-script", "target.b") == 0
    assert await _run(redis, "allow-script", "target.a") == 0
    assert await _run(redis, "allow-script", "target.b") == 0

    assert (await load_settings(redis)).allowed_scripts == ["target.b", "target.a"]


async def test_allow_script_that_is_already_listed_writes_nothing() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(REFLEX_SETTINGS_KEY, b'{"allowed_scripts":["target.a"]}')

    assert await _run(redis, "allow-script", " target.a ") == 0

    assert await redis.get(REFLEX_SETTINGS_KEY) == b'{"allowed_scripts":["target.a"]}'


async def test_allow_script_refuses_a_blank_id(capsys: pytest.CaptureFixture[str]) -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "allow-script", "  ") != 0

    assert await _stored(redis) is None
    assert "nothing saved" in capsys.readouterr().err


async def test_disallow_script_removes_one_keeps_order_and_is_idempotent() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await save_settings(redis, ReflexSettings(allowed_scripts=["target.a", "target.b", "target.c"]))

    assert await _run(redis, "disallow-script", "target.b") == 0
    assert await _run(redis, "disallow-script", "target.b") == 0

    assert (await load_settings(redis)).allowed_scripts == ["target.a", "target.c"]


async def test_disallow_script_with_nothing_stored_writes_nothing() -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await _run(redis, "disallow-script", "target.a") == 0

    assert await _stored(redis) is None
