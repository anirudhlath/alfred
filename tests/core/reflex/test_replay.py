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
from types import SimpleNamespace
from typing import TYPE_CHECKING

import fakeredis
import pytest
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
from core.reflex.replay import ERROR, Args, Outcome, ScoreArgs, parse_args, summarize
from core.reflex.replay_labels import Labelled
from core.reflex.settings import ReflexSettings
from sdk.alfred_sdk.live_state import LiveStateEntry, live_state_key
from shared.config import REFLEX_OUTPUTS, AlfredConfig
from shared.streams import (
    REFLEX_DECISION_LOG_STREAM,
    REFLEX_SETTINGS_KEY,
    TOOL_REGISTRY_KEY,
    USER_TIMEZONE_KEY,
)
from tests.core.reflex.fakes import reflex_agent
from tests.core.reflex.label_fakes import (
    BEDROOM,
    DAY,
    HOUSE,
    LAMP,
    LAMP_ON,
    MOTION,
    PENDANTS,
    act_reply,
    minutes,
    person_change,
    record_at,
    standard_log,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from core.reflex.agent import ReflexAgent
    from core.reflex.reversal import PersonChange
    from core.reflex.tool_registry import ToolInfo
    from shared.config import ReflexOutput
    from shared.types import AioRedis
    from tests.llm_fakes import Reply, Script

T0 = datetime(2026, 10, 26, 23, 40, tzinfo=UTC)
ISO = "2026-10-26T23:35:00Z"
NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)

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
        monkeypatch.setattr(replay, "build_agent", lambda _config, _model, _output: agent)
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


async def test_latency_is_the_model_call_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(replay, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    real_parse = replay.parse_tools

    def slow_parse(prompt: str) -> list[ToolInfo]:
        clock[0] += 5.0  # reading the tools back out of the prompt is not the model's time
        return real_parse(prompt)

    monkeypatch.setattr(replay, "parse_tools", slow_parse)
    agent, _ = reflex_agent(NONE)

    [outcome] = await replay.replay([_state_change(0, "none")], agent)

    assert outcome.latency_ms == 0


async def test_a_run_stopped_by_errors_names_them_and_guesses_no_cause(
    use_script: Callable[..., Script], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    limit = replay.MAX_CONSECUTIVE_ERRORS
    use_script(*[RuntimeError("Living Room Lamp down")] * (limit + 2))
    records = _jsonl(
        tmp_path / "records.jsonl", [_state_change(i, "none") for i in range(limit + 2)]
    )

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["run", "--records", str(records)]))

    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert f"{limit} consecutive errors" in err
    assert "RuntimeError" in err
    assert "model up" not in err  # a 400 or a bad prompt fails too: it guesses no cause
    assert "Living Room Lamp" not in err  # the class name only, never the message


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
        ["run", "--since", ISO, "--output", "xml"],
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


def _again(record: DecisionRecord, *, seconds: int = 1) -> DecisionRecord:
    """The same event recorded a second time, a little later: a crash before its marker."""
    return record.model_copy(update={"time": record.time + timedelta(seconds=seconds)})


def _without_event_id(record: DecisionRecord) -> DecisionRecord:
    event = {k: v for k, v in record.event.items() if k != "event_id"}
    return record.model_copy(update={"event": event})


async def test_an_event_recorded_twice_is_replayed_once(
    use_script: Callable[..., Script], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = use_script(NONE, NONE)
    first, other = _state_change(0, "none"), _trigger(2, "none")
    records = _jsonl(tmp_path / "records.jsonl", [first, _again(first), other])

    await replay.execute(parse_args(["run", "--records", str(records)]))

    shown = capsys.readouterr()
    assert script.call_count == 2
    assert [script.prompt(i) for i in range(2)] == [first.prompt, other.prompt]
    assert "Dropped 1 duplicate record" in shown.out
    assert "Replayed 2 of 2 records" in shown.out
    assert first.event["event_id"] not in shown.out + shown.err  # a count, never an id
    for private in PRIVATE:
        assert private not in shown.out + shown.err


async def test_duplicates_are_dropped_before_the_limit(
    use_script: Callable[..., Script], tmp_path: Path
) -> None:
    script = use_script(NONE, NONE)
    first, second = _state_change(0, "none"), _trigger(2, "none")  # a prompt of its own
    records = _jsonl(tmp_path / "records.jsonl", [first, _again(first), second])

    await replay.execute(parse_args(["run", "--records", str(records), "--limit", "2"]))

    assert script.call_count == 2
    assert script.prompt(1) == second.prompt


async def test_records_without_an_event_id_are_all_kept(
    use_script: Callable[..., Script], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = use_script(NONE, NONE)
    first = _without_event_id(_state_change(0, "none"))
    records = _jsonl(tmp_path / "records.jsonl", [first, _again(first)])

    await replay.execute(parse_args(["run", "--records", str(records)]))

    assert script.call_count == 2
    assert "duplicate" not in capsys.readouterr().out


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


@pytest.mark.usefixtures("restored_environ")
def test_the_model_option_overrides_the_configured_one() -> None:
    config = dataclasses.replace(
        AlfredConfig.from_env(),
        reflex_model="vllm:configured-model",
        vllm_base_url="http://vllm.test:8000/v1",
    )

    agent = replay.build_agent(config, "vllm:other-model", None)

    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "other-model"


@pytest.mark.usefixtures("restored_environ")
def test_without_any_model_the_replay_exits_non_zero() -> None:
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="")

    with pytest.raises(SystemExit) as stopped:
        replay.build_agent(config, None, None)

    assert stopped.value.code not in (0, None)


HOSTED = [
    "openrouter:some/model",
    "openai:some-model",
    "anthropic:some-model",
    "gateway/x:y",
    "served-name",
]


@pytest.mark.usefixtures("restored_environ")
@pytest.mark.parametrize("model", HOSTED)
def test_a_model_that_is_not_on_the_local_provider_is_refused_with_the_reason(
    model: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolved(*_: object) -> None:
        raise AssertionError("the model was resolved before it was refused")

    monkeypatch.setattr(replay, "resolve_model", resolved)
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="vllm:configured-model")

    with pytest.raises(SystemExit) as stopped:
        replay.build_agent(config, model, None)

    assert stopped.value.code not in (0, None)
    err = capsys.readouterr().err
    assert "vllm:" in err and "private" in err
    assert model not in err


@pytest.mark.usefixtures("restored_environ")
def test_a_configured_model_is_held_to_the_same_rule_and_named_as_the_setting() -> None:
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="openrouter:some/model")

    with pytest.raises(SystemExit) as stopped:
        replay.build_agent(config, None, None)

    assert stopped.value.code not in (0, None)


@pytest.mark.usefixtures("restored_environ")
def test_the_local_provider_is_allowed_however_it_is_spelled_around() -> None:
    config = dataclasses.replace(
        AlfredConfig.from_env(), reflex_model="  vllm:configured-model ", reflex_output="text"
    )

    assert replay.build_agent(config, None, None).model.model_name == "configured-model"


@pytest.mark.usefixtures("restored_environ")
async def test_run_refuses_a_hosted_model_before_it_reads_a_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _jsonl(tmp_path / "records.jsonl", _three())

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["run", "--records", str(records), "--model", "openai:m"]))

    assert stopped.value.code not in (0, None)
    assert "private" in capsys.readouterr().err


@pytest.mark.usefixtures("restored_environ")
async def test_score_refuses_a_hosted_model_before_it_asks_it_anything(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = _label_file(tmp_path / "labels.jsonl", [_labelled("none", _state_change(0, "none"))])

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["score", "--labels", str(labels), "--model", "openai:m"]))

    assert stopped.value.code not in (0, None)
    assert "private" in capsys.readouterr().err


# --- the output shape ---------------------------------------------------------------------


@pytest.mark.parametrize("shape", REFLEX_OUTPUTS)
def test_the_output_option_is_read_on_run_and_score(shape: str, tmp_path: Path) -> None:
    records = _jsonl(tmp_path / "records.jsonl", [_state_change(0, "none")])
    labels = _jsonl(tmp_path / "labels.jsonl", [])

    run = parse_args(["run", "--records", str(records), "--output", shape])
    score = parse_args(["score", "--labels", str(labels), "--output", shape])

    assert isinstance(run, Args)
    assert isinstance(score, ScoreArgs)
    assert (run.output, score.output) == (shape, shape)


def test_without_the_output_option_the_shape_is_left_to_the_configuration(
    tmp_path: Path,
) -> None:
    records = _jsonl(tmp_path / "records.jsonl", [_state_change(0, "none")])
    labels = _jsonl(tmp_path / "labels.jsonl", [])

    run = parse_args(["run", "--records", str(records)])
    score = parse_args(["score", "--labels", str(labels)])

    assert isinstance(run, Args)
    assert isinstance(score, ScoreArgs)
    assert (run.output, score.output) == (None, None)


@pytest.mark.usefixtures("restored_environ")
@pytest.mark.parametrize("shape", REFLEX_OUTPUTS)
def test_the_output_option_sets_the_agents_shape(shape: ReflexOutput) -> None:
    config = dataclasses.replace(
        AlfredConfig.from_env(), reflex_model="vllm:configured-model", reflex_output="text"
    )

    agent = replay.build_agent(config, None, shape)

    assert agent.output_shape == shape


@pytest.mark.usefixtures("restored_environ")
def test_without_the_output_option_the_agent_takes_the_configured_shape() -> None:
    config = dataclasses.replace(
        AlfredConfig.from_env(), reflex_model="vllm:configured-model", reflex_output="native"
    )

    agent = replay.build_agent(config, None, None)

    assert agent.output_shape == "native"


@pytest.mark.parametrize("command", ["run", "score"])
async def test_the_command_builds_its_agent_in_the_shape_asked_for(
    command: str,
    house: AioRedis,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent, _ = reflex_agent(NONE)
    built: list[tuple[str | None, str | None]] = []

    def build(_config: AlfredConfig, model: str | None, output: str | None) -> ReflexAgent:
        built.append((model, output))
        return agent

    monkeypatch.setattr(replay, "build_agent", build)
    record = _state_change(0, "none")
    if command == "run":
        records = _jsonl(tmp_path / "records.jsonl", [record])
        argv = ["run", "--records", str(records)]
    else:
        labels = _label_file(tmp_path / "labels.jsonl", [_labelled("none", record)])
        argv = ["score", "--labels", str(labels)]

    await replay.execute(parse_args([*argv, "--model", "vllm:served", "--output", "tool"]))

    assert built == [("vllm:served", "tool")]


# --- labels and scores --------------------------------------------------------------------

# What the label and score output must not name, on top of what `run`'s must not.
PRIVATE_HOUSE = (
    *PRIVATE,
    PENDANTS,
    BEDROOM,
    MOTION,
    "Kitchen Pendants",
    "Bedroom Lamp",
    "Living Room",
    "Kitchen",
    "Bedroom",
    "Garage Light",
)


def _label_file(path: Path, lines: Sequence[Labelled]) -> Path:
    path.write_text("".join(line.model_dump_json() + "\n" for line in lines))
    return path


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeServer:
    """A Redis with no live state, that the commands open instead of a real one."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        replay, "create_redis", lambda _url: fakeredis.FakeAsyncRedis(server=server)
    )
    return server


@pytest.fixture
async def house(server: fakeredis.FakeServer) -> AioRedis:
    """The same Redis, with the three lights in the live state."""
    redis = fakeredis.FakeAsyncRedis(server=server)
    await redis.hset(TOOL_REGISTRY_KEY, "home-service", "{}")
    for entity in HOUSE.values():
        entry = LiveStateEntry(
            domain=entity.domain,
            kind="controllable",
            state=entity.state,
            attributes=dict(entity.attributes),
        )
        await redis.hset(live_state_key("home-service"), entity.entity_id, entry.model_dump_json())
    return redis


# --- labels: the command ------------------------------------------------------------------


async def test_label_writes_a_private_file_and_prints_counts_only(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = standard_log()
    records = _jsonl(tmp_path / "records.jsonl", log)
    out = tmp_path / "labels.jsonl"
    out.write_text("old\n")
    out.chmod(0o644)

    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(out)]))

    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 9
    assert all(set(line) == {"label", "why", "changes", "record"} for line in lines)
    assert [(x["label"], x["why"]) for x in lines[:2]] == [
        ("none", "no_proposal"),
        ("none", "reverses"),
    ]
    assert lines[1]["record"]["prompt"] == log[1].prompt
    assert set(lines[1]["changes"]) == {BEDROOM}
    shown = capsys.readouterr()
    assert "Labelled 9 records: 3 ask, 6 none." in shown.out
    assert (
        "None by reason: no_proposal 2, unresolved 1, reverses 1, repeat 1, quiet_hours 1"
        in shown.out
    )
    for private in PRIVATE_HOUSE:
        assert private not in shown.out + shown.err


async def test_label_reads_the_live_log_and_writes_nothing_to_redis(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await _logged(
        house,
        [
            record_at(DAY - minutes(90), decision="act", target="Kitchen Pendants"),  # too early
            *standard_log()[:3],
            record_at(DAY + minutes(300), decision="act", target="Kitchen Pendants"),  # at until
        ],
    )
    before = (await house.dbsize(), await house.xlen(REFLEX_DECISION_LOG_STREAM))
    out = tmp_path / "labels.jsonl"

    await replay.execute(
        parse_args(
            [
                "label",
                "--since",
                "2026-10-27T13:00:00Z",
                "--until",
                "2026-10-27T19:00:00Z",
                "--out",
                str(out),
            ]
        )
    )

    assert (await house.dbsize(), await house.xlen(REFLEX_DECISION_LOG_STREAM)) == before
    assert len(out.read_text().splitlines()) == 3  # neither the one before since nor at until
    assert "Labelled 3 records: 1 ask, 2 none." in capsys.readouterr().out


async def test_label_uses_the_stored_settings_and_zone(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quiet = ReflexSettings(quiet_hours_start="12:30", quiet_hours_end="13:30")
    await house.set(REFLEX_SETTINGS_KEY, quiet.model_dump_json())
    await house.set(USER_TIMEZONE_KEY, "America/Los_Angeles")
    # 20:00 UTC is 13:00 there: quiet only by the stored window and the stored zone together.
    asked_at = DAY + minutes(360)
    records = _jsonl(
        tmp_path / "records.jsonl",
        [record_at(asked_at, decision="act", target="Kitchen Pendants")],
    )
    out = tmp_path / "labels.jsonl"

    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(out)]))

    assert json.loads(out.read_text())["why"] == "quiet_hours"
    assert "Labelled 1 record: 0 ask, 1 none." in capsys.readouterr().out


async def test_label_drops_an_event_recorded_twice(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = record_at(DAY, decision="act", target="Kitchen Pendants")
    records = _jsonl(tmp_path / "records.jsonl", [first, _again(first, seconds=30)])
    out = tmp_path / "labels.jsonl"

    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(out)]))

    shown = capsys.readouterr().out
    assert "Dropped 1 duplicate record." in shown
    assert "Labelled 1 record: 1 ask, 0 none." in shown
    assert len(out.read_text().splitlines()) == 1


async def test_label_without_live_state_exits_one_and_writes_nothing(
    server: fakeredis.FakeServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _jsonl(tmp_path / "records.jsonl", standard_log())
    out = tmp_path / "labels.jsonl"

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["label", "--records", str(records), "--out", str(out)]))

    assert stopped.value.code == 1
    assert "live state" in capsys.readouterr().err
    assert not out.exists()


async def test_a_line_that_is_not_a_record_stops_label_naming_its_number_only(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(f'{standard_log()[0].model_dump_json()}\n{{"prompt": "Kitchen Pendants"}}\n')

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(
            parse_args(["label", "--records", str(path), "--out", str(tmp_path / "out.jsonl")])
        )

    assert stopped.value.code == 2
    shown = capsys.readouterr()
    assert "line 2" in shown.err
    assert "Kitchen Pendants" not in shown.out + shown.err


@pytest.mark.parametrize(
    "argv",
    [
        ["label"],
        ["label", "--records", "records.jsonl"],  # no --out
        ["label", "--out", "labels.jsonl"],  # no source
        ["label", "--since", ISO, "--records", "records.jsonl", "--out", "labels.jsonl"],
        ["label", "--records", "records.jsonl", "--until", ISO, "--out", "labels.jsonl"],
        ["label", "--since", ISO, "--until", "2026-10-26T23:00:00Z", "--out", "labels.jsonl"],
        ["label", "--records", "missing.jsonl", "--out", "labels.jsonl"],
        ["score"],
        ["score", "--model", "vllm:served"],
        ["score", "--labels", "missing.jsonl"],
        ["score", "--labels", "labels.jsonl", "--output", "xml"],
    ],
)
def test_a_wrong_label_or_score_command_line_is_a_usage_error(
    argv: list[str], tmp_path: Path
) -> None:
    (tmp_path / "records.jsonl").write_text("")

    with pytest.raises(SystemExit) as stopped:
        parse_args([str(tmp_path / a) if a.endswith(".jsonl") else a for a in argv])

    assert stopped.value.code == 2


# --- scores -------------------------------------------------------------------------------


def _labelled(
    label: str,
    record: DecisionRecord,
    why: str | None = None,
    changes: dict[str, PersonChange] | None = None,
) -> Labelled:
    return Labelled.model_validate(
        {"label": label, "why": why, "changes": changes or {}, "record": record}
    )


def _scored_lines() -> list[Labelled]:
    """Nine lines; with `_scored_replies` each lands in a different count."""
    ask = {"decision": "act", "target": "Kitchen Pendants"}
    lines = [
        _labelled("none", record_at(DAY), "no_proposal"),  # the model will act: a wrong ask
        _labelled("ask", record_at(DAY + minutes(1), **ask)),  # the model will say none: missed
        _labelled("ask", record_at(DAY + minutes(2), **ask)),  # it will ask about another light
        _labelled("ask", record_at(DAY + minutes(3), **ask)),  # it will ask the same: matched
        _labelled(  # it will act on a lamp a person just changed
            "none",
            record_at(DAY + minutes(4), decision="act", target="Living Room Lamp"),
            "reverses",
            {LAMP: person_change(DAY + minutes(1))},
        ),
        _labelled("ask", record_at(DAY + minutes(5), **ask)),  # no decision: invalid
        _labelled("ask", record_at(DAY + minutes(6), **ask)),  # a light there is not: invalid
        _labelled("none", record_at(DAY + minutes(7)), "no_proposal"),  # none and none: right
        _labelled("ask", record_at(DAY + minutes(8), **ask)),  # the call fails
    ]
    return lines


def _scored_replies() -> list[str | BaseException]:
    return [
        act_reply("Living Room Lamp"),
        NONE,
        act_reply("Living Room Lamp"),
        act_reply("Kitchen Pendants"),
        act_reply("Living Room Lamp"),
        "this is not a decision",
        act_reply("Garage Light"),
        NONE,
        RuntimeError("Living Room Lamp down"),
    ]


async def test_score_counts_wrong_missed_and_other_asks_and_the_rest(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = _scored_lines()
    script = use_script(*_scored_replies())
    labels = _label_file(tmp_path / "labels.jsonl", lines)

    await replay.execute(parse_args(["score", "--labels", str(labels), "--model", "vllm:served"]))

    shown = capsys.readouterr().out
    assert [script.prompt(i) for i in range(9)] == [line.record.prompt for line in lines]
    assert "Scored 9 of 9 records, 1 failed." in shown
    wrong = "Wrong asks: 1 of 3 labelled none for no_proposal, unresolved, reverses, repeat (33.3%)"
    assert wrong in shown
    assert "Asks the gate would hold: 0 of 0 labelled none for repeat, quiet_hours" in shown
    assert "Missed asks: 1 of 5 labelled ask (20.0%)" in shown
    assert "Other asks: 1 of 5 labelled ask (20.0%)" in shown
    assert "Matched asks: 1 of 5 labelled ask (20.0%)" in shown
    assert "Would reverse a person: 1 (must be 0)" in shown
    assert "Invalid replies: 2 of 8 (25.0%)" in shown
    assert "Model latency: p50" in shown and "p95" in shown
    assert "Failed calls: RuntimeError x1" in shown


@pytest.mark.parametrize(("why", "judged"), [("repeat", 3), ("quiet_hours", 1)])
async def test_score_does_not_count_an_ask_the_gate_would_hold_as_a_wrong_ask(
    why: str,
    judged: int,
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The prompt shows the model neither the asks made lately nor quiet hours: production's
    # gate holds those in code, so a model that proposes one is not wrong.
    use_script(act_reply("Kitchen Pendants"), act_reply("Kitchen Pendants"), NONE)
    record = record_at(DAY, decision="act", target="Kitchen Pendants")
    labels = _label_file(
        tmp_path / "labels.jsonl",
        [
            _labelled("none", record, why),  # the model asks: the gate would hold it
            _labelled("none", record, "unresolved"),  # the model asks: wrong
            _labelled("none", record, why),  # the model says none: nothing to count
        ],
    )

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr().out
    wrong = f"Wrong asks: 1 of {judged} labelled none for no_proposal, unresolved, reverses, repeat"
    assert f"{wrong} ({100 / judged:.1f}%)" in shown  # a repeat line is one a wrong ask can be on
    assert "Asks the gate would hold: 1 of 2 labelled none for repeat, quiet_hours (50.0%)" in shown
    assert "Missed asks: 0 of 0 labelled ask" in shown  # a gated none is not a missed ask either


async def test_score_holds_a_repeat_only_for_the_ask_that_was_repeated(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The gate's repeat key is the tool and the targets: an ask about another light would
    # really be put to the owner.
    use_script(act_reply("Kitchen Pendants"), act_reply("Living Room Lamp"))
    record = record_at(DAY, decision="act", target="Kitchen Pendants")
    labels = _label_file(
        tmp_path / "labels.jsonl",
        [_labelled("none", record, "repeat"), _labelled("none", record, "repeat")],
    )

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr().out
    assert "Wrong asks: 1 of 2 labelled none for no_proposal, unresolved, reverses, repeat" in shown
    assert "Asks the gate would hold: 1 of 2 labelled none for repeat, quiet_hours (50.0%)" in shown


async def test_score_says_record_for_one_record(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_script(NONE)
    labels = _label_file(
        tmp_path / "labels.jsonl", [_labelled("none", record_at(DAY), "no_proposal")]
    )

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    assert "Scored 1 of 1 record, 0 failed." in capsys.readouterr().out


async def test_score_names_nothing_of_the_house(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_script(*_scored_replies())
    labels = _label_file(tmp_path / "labels.jsonl", _scored_lines())

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr()
    assert "Wrong asks" in shown.out  # it did print its counts
    for private in PRIVATE_HOUSE:
        assert private not in shown.out + shown.err


async def test_score_judges_the_candidate_by_the_changes_the_line_was_labelled_with(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_script(act_reply("Living Room Lamp"), act_reply("Living Room Lamp"))
    record = record_at(DAY, decision="act", target="Kitchen Pendants")
    labels = _label_file(
        tmp_path / "labels.jsonl",
        [
            _labelled("ask", record),  # no change: the lamp is fair game
            _labelled("none", record, "reverses", {LAMP: person_change(DAY - minutes(5))}),
        ],
    )

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr().out
    assert "Other asks: 1 of 1 labelled ask" in shown
    assert "Would reverse a person: 1 (must be 0)" in shown
    assert "Wrong asks: 0 of 1 labelled none for no_proposal, unresolved, reverses, repeat" in shown


async def test_a_candidate_that_would_reverse_a_person_does_not_change_the_exit_status(
    house: AioRedis, use_script: Callable[..., Script], tmp_path: Path
) -> None:
    use_script(act_reply("Living Room Lamp"))
    record = record_at(DAY, decision="act", target="Living Room Lamp")
    labels = _label_file(
        tmp_path / "labels.jsonl",
        [_labelled("none", record, "reverses", {LAMP: person_change(DAY - minutes(5))})],
    )

    await replay.execute(parse_args(["score", "--labels", str(labels)]))  # does not raise


async def test_score_stops_after_consecutive_errors_naming_their_class_only(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    limit = replay.MAX_CONSECUTIVE_ERRORS
    script = use_script(*[RuntimeError("Kitchen Pendants down")] * (limit + 2))
    lines = [
        _labelled("ask", record_at(DAY + minutes(i), decision="act", target="Kitchen Pendants"))
        for i in range(limit + 2)
    ]
    labels = _label_file(tmp_path / "labels.jsonl", lines)

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr()
    assert stopped.value.code == 1
    assert script.call_count == limit
    assert f"Scored {limit} of {limit + 2} records, {limit} failed." in shown.out
    assert f"{limit} consecutive errors" in shown.err
    assert "RuntimeError" in shown.err
    assert "Kitchen Pendants" not in shown.out + shown.err


async def test_score_without_live_state_exits_one_and_asks_the_model_nothing(
    server: fakeredis.FakeServer,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = use_script()
    labels = _label_file(tmp_path / "labels.jsonl", _scored_lines())

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["score", "--labels", str(labels)]))

    assert stopped.value.code == 1
    assert "live state" in capsys.readouterr().err
    assert script.call_count == 0


async def test_score_writes_nothing_to_redis(
    house: AioRedis, use_script: Callable[..., Script], tmp_path: Path
) -> None:
    use_script(*_scored_replies())
    labels = _label_file(tmp_path / "labels.jsonl", _scored_lines())
    before = (await house.dbsize(), sorted(await house.keys("*")))

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    assert (await house.dbsize(), sorted(await house.keys("*"))) == before


async def test_a_line_that_is_not_a_label_stops_score_naming_its_number_only(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = use_script()
    path = tmp_path / "labels.jsonl"
    good = _labelled("none", record_at(DAY), "no_proposal").model_dump_json()
    path.write_text(f'{good}\n{{"label": "ask", "why": "Kitchen Pendants"}}\n')

    with pytest.raises(SystemExit) as stopped:
        await replay.execute(parse_args(["score", "--labels", str(path)]))

    assert stopped.value.code == 2
    shown = capsys.readouterr()
    assert "line 2" in shown.err
    assert "Kitchen Pendants" not in shown.out + shown.err
    assert script.call_count == 0


async def test_score_reads_the_file_label_wrote(
    house: AioRedis,
    use_script: Callable[..., Script],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    records = _jsonl(tmp_path / "records.jsonl", standard_log())
    labels = tmp_path / "labels.jsonl"
    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(labels)]))
    capsys.readouterr()
    use_script(*[NONE] * 9)

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr().out
    assert "Scored 9 of 9 records, 0 failed." in shown
    assert "Missed asks: 3 of 3 labelled ask (100.0%)" in shown
    assert "Asks the gate would hold: 0 of 2 labelled none for repeat, quiet_hours (0.0%)" in shown
