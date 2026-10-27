"""``python -m core.reflex.replay``: recorded decisions run again through a model.

The replay reads recorded prompts and calls the model, one record at a time. It writes nothing
to Redis and publishes nothing, and what it prints is counts only: the prompts name the house
and its people, so neither they nor the reasons they led to may reach the terminal.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import fakeredis
import pytest
from core.reflex.replay import ERROR, Outcome, parse_args, summarize
from pydantic_ai.models.openai import OpenAIChatModel

from bus.schemas.events import (
    ActionRequest,
    ReflexDecision,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.reflex import replay
from core.reflex.decision_log import DecisionRecord
from core.reflex.prompt import build_state_change_prompt, build_trigger_prompt
from core.reflex.prose import repo_prose
from core.reflex.tool_registry import ToolInfo
from shared.config import AlfredConfig
from shared.streams import REFLEX_DECISION_LOG_STREAM
from tests.core.reflex.fakes import reflex_agent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shared.types import AioRedis
    from tests.llm_fakes import Reply, Script

T0 = datetime(2026, 10, 26, 23, 40, tzinfo=UTC)
ISO = "2026-10-26T23:35:00Z"
NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)

LAMP_ON = ToolInfo(
    name="home.light_turn_on",
    description="light.turn_on",
    feature_name="home",
    feature_description="",
    target_service="home-service",
    audience="reflex",
    input_schema={
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    },
)

# What must never reach the terminal: names, ids, reasons and the prompt's own words.
PRIVATE = (
    "light.living_room_lamp",
    "Living Room Lamp",
    "the lamp was left on",
    "bedtime",
    "## Preferences",
    "Tools:",
)

NONE = '{"decision": "none"}'
ACT = (
    '{"decision": "act", "tool_name": "home.light_turn_on", '
    '"parameters": {"target": "Living Room Lamp"}, "reason": "dark"}'
)


def _action() -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"target": "Living Room Lamp"},
    )


def _state_change(minute: int, recorded: ReflexDecision) -> DecisionRecord:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room_lamp",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room Lamp", "area": "Living Room"},
    )
    prompt = build_state_change_prompt(
        event=event,
        preferences="- Prefers dim light for films",
        tools=[LAMP_ON],
        entities=None,
        now=T0,
        tz_name="UTC",
        prose=repo_prose("state_change"),
    )
    return DecisionRecord(
        time=T0 + timedelta(minutes=minute),
        origin="state_change",
        event=event.model_dump(mode="json"),
        cause=None,
        prompt=prompt,
        proposal=ReflexProposal(
            decision=recorded,
            reason="the lamp was left on",
            action=_action() if recorded in ("act", "ask") else None,
        ),
    )


def _trigger(minute: int, recorded: ReflexDecision) -> DecisionRecord:
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    prompt = build_trigger_prompt(
        event=event,
        preferences="",
        tools=[LAMP_ON],
        entities=None,
        now=T0,
        tz_name="UTC",
        prose=repo_prose("trigger_fired"),
    )
    return DecisionRecord(
        time=T0 + timedelta(minutes=minute),
        origin="trigger_fired",
        event=event.model_dump(mode="json"),
        cause=None,
        prompt=prompt,
        proposal=ReflexProposal(decision=recorded, reason="the lamp was left on"),
    )


def _three() -> list[DecisionRecord]:
    """Recorded none, act and ask; with the replies below they replay as none, act, invalid."""
    return [_state_change(0, "none"), _state_change(1, "act"), _trigger(2, "ask")]


def _table(text: str) -> dict[str, dict[str, int]]:
    """The recorded-by-replayed table as printed: {recorded: {replayed: count}}."""
    lines = text.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("recorded"))
    columns = lines[head].split()[3:]  # after "recorded \ replayed"
    table: dict[str, dict[str, int]] = {}
    for line in lines[head + 1 :]:
        cells = line.split()
        if not cells:
            break
        table[cells[0]] = dict(zip(columns, map(int, cells[1:]), strict=True))
    return table


def _jsonl(path: Path, records: list[DecisionRecord]) -> Path:
    path.write_text("".join(r.model_dump_json() + "\n" for r in records))
    return path


@pytest.fixture
def use_script(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Script]:
    """``use_script(*replies)`` makes the command build a scripted agent, never a real model."""

    def use(*replies: Reply) -> Script:
        agent, script = reflex_agent(*replies)
        monkeypatch.setattr(replay, "build_agent", lambda _config, _model: agent)
        return script

    return use


# --- the counts ---------------------------------------------------------------------------


async def test_replay_counts() -> None:
    agent, script = reflex_agent(NONE, ACT, "this is not a decision")
    records = _three()

    outcomes = await replay.replay(records, agent)
    text = summarize(outcomes, total=len(records))

    assert [(o.recorded, o.replayed) for o in outcomes] == [
        ("none", "none"),
        ("act", "act"),  # an act that names a tool the prompt showed: the tools were parsed
        ("ask", "invalid"),
    ]
    assert [script.prompt(i) for i in range(3)] == [r.prompt for r in records]
    table = _table(text)
    assert table["none"]["none"] == 1
    assert table["act"]["act"] == 1
    assert table["ask"]["invalid"] == 1
    assert sum(sum(row.values()) for row in table.values()) == 3
    assert "Replayed 3 of 3 records" in text
    assert "Agreement: 2 of 3 (66.7%)" in text
    assert "1 of 3 replayed (33.3%)" in text
    assert "p50" in text and "p95" in text
    for private in PRIVATE:
        assert private not in text


async def test_a_failed_call_is_counted_and_the_run_goes_on() -> None:
    agent, _ = reflex_agent(RuntimeError("model down"), NONE)
    records = [_state_change(0, "none"), _state_change(1, "none")]

    outcomes = await replay.replay(records, agent)
    text = summarize(outcomes, total=len(records))

    assert [o.replayed for o in outcomes] == [ERROR, "none"]
    assert outcomes[0].latency_ms is None
    assert _table(text)["none"] == {"act": 0, "ask": 0, "none": 1, "invalid": 0, "error": 1}
    assert "Agreement: 1 of 1 (100.0%)" in text  # a failed call is neither agreement nor not
    assert "RuntimeError" in text
    assert "model down" not in text


async def test_a_server_that_keeps_failing_stops_the_run_early() -> None:
    limit = replay.MAX_CONSECUTIVE_ERRORS
    agent, script = reflex_agent(*[RuntimeError("down")] * (limit + 5))
    records = [_state_change(i, "none") for i in range(limit + 5)]

    outcomes = await replay.replay(records, agent)

    assert len(outcomes) == limit == script.call_count
    assert "Replayed 5 of 10 records" in summarize(outcomes, total=len(records))


async def test_a_success_resets_the_run_of_failures() -> None:
    limit = replay.MAX_CONSECUTIVE_ERRORS
    failing = [RuntimeError("down")] * (limit - 1)
    agent, _ = reflex_agent(*failing, NONE, *failing, NONE)
    records = [_state_change(i, "none") for i in range(2 * limit)]

    assert len(await replay.replay(records, agent)) == 2 * limit


def test_latency_percentiles_are_nearest_rank() -> None:
    outcomes = [
        Outcome(time=T0, origin="state_change", recorded="none", replayed="none", latency_ms=ms)
        for ms in (100, 10, 90, 20, 80, 30, 70, 40, 60, 50)
    ]

    assert "Model latency: p50 50 ms, p95 100 ms" in summarize(outcomes, total=10)


# --- the command line ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["run"],
        ["run", "--since", ISO, "--records", "records.jsonl"],
        ["run", "--records", "records.jsonl", "--until", ISO],
        ["run", "--since", "2026-10-26T23:35:00"],  # no UTC offset
        ["run", "--since", "yesterday"],
        ["run", "--since", ISO, "--until", "2026-10-26T23:00:00Z"],
        ["run", "--since", ISO, "--limit", "0"],
        ["run", "--records", "missing.jsonl"],
    ],
)
def test_a_wrong_command_line_is_a_usage_error(argv: list[str], tmp_path: Path) -> None:
    (tmp_path / "records.jsonl").write_text("")

    with pytest.raises(SystemExit) as stopped:
        parse_args([str(tmp_path / a) if a.endswith(".jsonl") else a for a in argv])

    assert stopped.value.code == 2


def test_help_says_which_records_a_limit_keeps(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        parse_args(["run", "--help"])

    assert stopped.value.code == 0
    assert "oldest first" in " ".join(capsys.readouterr().out.split())


async def test_records_from_a_file_print_counts_only(
    use_script: Callable[..., Script], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    use_script(NONE, ACT, "this is not a decision")
    records = _jsonl(tmp_path / "records.jsonl", _three())

    await replay.execute(parse_args(["run", "--records", str(records)]))

    shown = capsys.readouterr()
    assert _table(shown.out)["act"]["act"] == 1
    for private in PRIVATE:
        assert private not in shown.out + shown.err


async def test_the_out_file_is_private_and_has_one_line_per_record(
    use_script: Callable[..., Script], tmp_path: Path
) -> None:
    use_script(NONE, ACT, "this is not a decision")
    records = _jsonl(tmp_path / "records.jsonl", _three())
    out = tmp_path / "replay.jsonl"
    out.write_text("old\n")
    out.chmod(0o644)

    await replay.execute(parse_args(["run", "--records", str(records), "--out", str(out)]))

    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(r["recorded"], r["replayed"]) for r in lines] == [
        ("none", "none"),
        ("act", "act"),
        ("ask", "invalid"),
    ]
    assert [r["origin"] for r in lines] == ["state_change", "state_change", "trigger_fired"]
    assert datetime.fromisoformat(lines[0]["time"]) == _three()[0].time
    assert all(set(r) == {"time", "origin", "recorded", "replayed", "latency_ms"} for r in lines)
    assert all(isinstance(r["latency_ms"], float) for r in lines)


async def test_a_new_out_file_is_private_too(
    use_script: Callable[..., Script], tmp_path: Path
) -> None:
    use_script(NONE)
    records = _jsonl(tmp_path / "records.jsonl", [_state_change(0, "none")])
    out = tmp_path / "fresh.jsonl"

    await replay.execute(parse_args(["run", "--records", str(records), "--out", str(out)]))

    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


async def test_limit_replays_the_oldest_records_first(
    use_script: Callable[..., Script], tmp_path: Path
) -> None:
    script = use_script(NONE, NONE)
    # Written newest first: the limit still keeps the two oldest.
    records = _jsonl(
        tmp_path / "records.jsonl",
        [_trigger(2, "none"), _state_change(1, "none"), _state_change(0, "none")],
    )
    expected = sorted(
        [_trigger(2, "none"), _state_change(1, "none"), _state_change(0, "none")],
        key=lambda r: r.time,
    )[:2]

    await replay.execute(parse_args(["run", "--records", str(records), "--limit", "2"]))

    assert script.call_count == 2
    assert [script.prompt(i) for i in range(2)] == [r.prompt for r in expected]


async def test_a_line_that_is_not_a_record_names_its_number_and_nothing_else(
    use_script: Callable[..., Script], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = use_script()
    path = tmp_path / "records.jsonl"
    good = _state_change(0, "none").model_dump_json()
    path.write_text(f'{good}\n{{"prompt": "Living Room Lamp", "time": 5}}\n')

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["run", "--records", str(path)]))

    assert stopped.value.code == 2
    shown = capsys.readouterr()
    assert "line 2" in shown.err
    assert "Living Room Lamp" not in shown.out + shown.err
    assert script.call_count == 0


# --- the live log -------------------------------------------------------------------------


async def _logged(redis: AioRedis, records: list[DecisionRecord]) -> None:
    for record in records:
        entry_ms = int(record.time.timestamp() * 1000)
        await redis.xadd(
            REFLEX_DECISION_LOG_STREAM, {"decision": record.model_dump_json()}, id=f"{entry_ms}-0"
        )


async def test_since_and_until_read_the_live_log_and_write_nothing(
    use_script: Callable[..., Script],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = fakeredis.FakeServer()
    redis = fakeredis.FakeAsyncRedis(server=server)
    await _logged(
        redis,
        [
            _state_change(-10, "none"),  # before since
            _state_change(1, "none"),
            _state_change(2, "none"),
            _state_change(30, "none"),  # at until
        ],
    )
    script = use_script(NONE, NONE)
    monkeypatch.setattr(
        replay, "create_redis", lambda _url: fakeredis.FakeAsyncRedis(server=server)
    )
    before = (await redis.dbsize(), await redis.xlen(REFLEX_DECISION_LOG_STREAM))

    await replay.execute(
        parse_args(["run", "--since", "2026-10-26T23:35:00Z", "--until", "2026-10-27T00:10:00Z"])
    )

    assert script.call_count == 2
    assert (await redis.dbsize(), await redis.xlen(REFLEX_DECISION_LOG_STREAM)) == before
    assert _table(capsys.readouterr().out)["none"]["none"] == 2


async def test_a_records_file_needs_no_redis(
    use_script: Callable[..., Script], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_script(NONE)

    def refused(_url: str) -> None:
        raise AssertionError("a records file must not open Redis")

    monkeypatch.setattr(replay, "create_redis", refused)
    records = _jsonl(tmp_path / "records.jsonl", [_state_change(0, "none")])

    await replay.execute(parse_args(["run", "--records", str(records)]))


async def test_no_records_is_a_clear_message_not_a_table(
    use_script: Callable[..., Script], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = use_script()
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")

    await replay.execute(parse_args(["run", "--records", str(empty)]))

    assert "No records to replay" in capsys.readouterr().out
    assert script.call_count == 0


# --- the model ----------------------------------------------------------------------------


def test_the_model_option_overrides_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_BASE_URL", "")
    monkeypatch.delenv("VLLM_BASE_URL")  # build_agent exports it; have the test undo that
    config = dataclasses.replace(
        AlfredConfig.from_env(),
        reflex_model="vllm:configured-model",
        vllm_base_url="http://vllm.test:8000/v1",
    )

    agent = replay.build_agent(config, "vllm:other-model")

    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "other-model"


def test_without_any_model_the_replay_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="")

    with pytest.raises(SystemExit) as stopped:
        replay.build_agent(config, None)

    assert stopped.value.code not in (0, None)
