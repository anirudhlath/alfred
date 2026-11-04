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
    ChangeContext,
    ReflexDecision,
    ReflexOutcome,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.attribution.cause import cause_of
from core.reflex import replay
from core.reflex.decision_log import DecisionRecord
from core.reflex.prompt import LiveEntity, build_state_change_prompt, build_trigger_prompt
from core.reflex.prose import repo_prose
from core.reflex.replay import ERROR, Labelled, Outcome, label_records, parse_args, summarize
from core.reflex.reversal import PersonChange
from core.reflex.settings import ReflexSettings
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.live_state import LiveStateEntry, live_state_key
from shared.config import AlfredConfig
from shared.streams import (
    REFLEX_DECISION_LOG_STREAM,
    REFLEX_SETTINGS_KEY,
    TOOL_REGISTRY_KEY,
    USER_TIMEZONE_KEY,
)
from tests.core.reflex.fakes import reflex_agent

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
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

    agent = replay.build_agent(config, "vllm:other-model")

    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "other-model"


@pytest.mark.usefixtures("restored_environ")
def test_without_any_model_the_replay_exits_non_zero() -> None:
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="")

    with pytest.raises(SystemExit) as stopped:
        replay.build_agent(config, None)

    assert stopped.value.code not in (0, None)


# --- labels and scores --------------------------------------------------------------------

DAY = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)  # mid-afternoon: outside the default quiet hours
LAMP = "light.living_room_lamp"
PENDANTS = "light.kitchen_pendants"
BEDROOM = "light.bedroom_lamp"
MOTION = "binary_sensor.kitchen_motion"  # not a device Reflex notes a person's change to
PERSON = ChangeContext(id="ctx-person", user_id="user-1")
ALFRED = ChangeContext(id="ctx-alfred", by_service=True)

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


def _entity(entity_id: str, name: str, area: str) -> LiveEntity:
    return LiveEntity(
        entity_id=entity_id,
        domain=entity_id.split(".")[0],
        controllable=True,
        state="off",
        attributes={"friendly_name": name, "area": area},
    )


HOUSE = {
    e.entity_id: e
    for e in (
        _entity(LAMP, "Living Room Lamp", "Living Room"),
        _entity(PENDANTS, "Kitchen Pendants", "Kitchen"),
        _entity(BEDROOM, "Bedroom Lamp", "Bedroom"),
    )
}


def _minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


def _change(at: datetime) -> PersonChange:
    return PersonChange(
        time=at,
        cause="person",
        old_state="off",
        new_state="on",
        context_id="ctx-person",
        attributes_only=False,
    )


def _decision(
    at: datetime,
    *,
    entity: str = MOTION,
    old: str | None = "off",
    new: str = "on",
    context: ChangeContext | None = None,
    decision: ReflexDecision = "none",
    target: str | None = None,
    outcome: ReflexOutcome | None = None,
    recent: dict[str, PersonChange] | None = None,
) -> DecisionRecord:
    """A decision about a state change at *at*; one with a *target* acts through the lamp tool."""
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity,
        old_state=old,
        new_state=new,
        context=context,
        timestamp=at - timedelta(seconds=2),
    )
    prompt = build_state_change_prompt(
        event=event,
        preferences="",
        tools=[LAMP_ON],
        entities=None,
        now=at,
        tz_name="UTC",
        prose=repo_prose("state_change"),
    )
    action = (
        None
        if target is None
        else ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name=LAMP_ON.name,
            parameters={"target": target},
        )
    )
    return DecisionRecord(
        time=at,
        origin="state_change",
        event=event.model_dump(mode="json"),
        cause=cause_of(context),
        prompt=prompt,
        proposal=ReflexProposal(decision=decision, reason="the lamp was left on", action=action),
        outcome=outcome,
        recent=recent or {},
    )


def _fired(at: datetime, *, decision: ReflexDecision, target: str) -> DecisionRecord:
    """A decision about a fired trigger, which carries no state change to fold."""
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    prompt = build_trigger_prompt(
        event=event,
        preferences="",
        tools=[LAMP_ON],
        entities=None,
        now=at,
        tz_name="UTC",
        prose=repo_prose("trigger_fired"),
    )
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=LAMP_ON.name,
        parameters={"target": target},
    )
    return DecisionRecord(
        time=at,
        origin="trigger_fired",
        event=event.model_dump(mode="json"),
        cause=None,
        prompt=prompt,
        proposal=ReflexProposal(decision=decision, reason="the lamp was left on", action=action),
    )


def _verdicts(
    records: Sequence[DecisionRecord],
    settings: ReflexSettings | None = None,
    tz_name: str = "UTC",
) -> list[tuple[str, str | None]]:
    labelled = label_records(records, HOUSE, settings or ReflexSettings(), tz_name)
    return [(line.label, line.why) for line in labelled]


def _act(target: str) -> str:
    return json.dumps(
        {
            "decision": "act",
            "tool_name": "home.light_turn_on",
            "parameters": {"target": target},
            "reason": "dark",
        }
    )


def _label_file(path: Path, lines: Sequence[Labelled]) -> Path:
    path.write_text("".join(line.model_dump_json() + "\n" for line in lines))
    return path


def _log() -> list[DecisionRecord]:
    """A reversal, a repeat, quiet hours and asks, oldest first."""
    return [
        # A person turns the bedroom lamp on; nothing is proposed.
        _decision(DAY, entity=BEDROOM, new="on", context=PERSON),
        # Ten minutes on, the model would switch that same lamp: it reverses the person.
        _decision(DAY + _minutes(10), decision="act", target="Bedroom Lamp"),
        _decision(DAY + _minutes(20), decision="ask", target="Kitchen Pendants"),  # an ask
        _decision(DAY + _minutes(30), decision="act", target="Living Room Lamp"),  # another target
        _decision(DAY + _minutes(50), decision="act", target="Kitchen Pendants"),  # a repeat
        _decision(DAY + _minutes(200), decision="act", target="Kitchen Pendants"),  # window over
        _decision(DAY + _minutes(210), decision="act", target="Garage Light"),  # no such light
        _decision(DAY + _minutes(215), decision="invalid"),  # the model's reply was no decision
        _decision(DAY + _minutes(570), decision="act", target="Living Room Lamp"),  # 23:30
    ]


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


# --- labels: the rules --------------------------------------------------------------------


def test_labels_cover_a_reversal_a_repeat_quiet_hours_and_asks() -> None:
    assert _verdicts(_log()) == [
        ("none", "no_proposal"),
        ("none", "reverses"),
        ("ask", None),
        ("ask", None),
        ("none", "repeat"),
        ("ask", None),
        ("none", "unresolved"),
        ("none", "no_proposal"),
        ("none", "quiet_hours"),
    ]


def test_a_label_carries_the_changes_it_was_judged_against() -> None:
    log = _log()

    labelled = label_records(log, HOUSE, ReflexSettings(), "UTC")

    assert set(labelled[1].changes) == {BEDROOM}  # rebuilt from the log's earlier record
    assert labelled[1].changes[BEDROOM].cause == "person"
    assert labelled[5].changes == {}  # that change is past the window by then
    assert labelled[1].record == log[1]


def test_a_person_change_within_the_window_is_reversed_and_past_it_is_not() -> None:
    def log(minutes: int) -> list[DecisionRecord]:
        return [
            _decision(DAY, entity=BEDROOM, context=PERSON),
            _decision(DAY + _minutes(minutes), decision="act", target="Bedroom Lamp"),
        ]

    assert _verdicts(log(59))[1] == ("none", "reverses")
    assert _verdicts(log(61))[1] == ("ask", None)
    assert _verdicts(log(61), ReflexSettings(reverse_window_minutes=120))[1] == ("none", "reverses")


def test_the_event_a_record_decided_about_counts_as_a_change_it_must_not_reverse() -> None:
    # The runner notes a change before the engine reads what people just changed.
    record = _decision(
        DAY, entity=LAMP, new="on", context=PERSON, decision="act", target="Living Room Lamp"
    )

    assert _verdicts([record]) == [("none", "reverses")]


def test_a_change_by_alfred_replaces_a_person_s_as_it_does_in_the_record() -> None:
    records = [
        _decision(DAY, entity=LAMP, new="on", context=PERSON),
        _decision(DAY + _minutes(5), entity=LAMP, old="on", new="off", context=ALFRED),
        _decision(DAY + _minutes(10), decision="act", target="Living Room Lamp"),
    ]

    assert _verdicts(records)[2] == ("ask", None)


def test_a_change_to_something_reflex_does_not_note_is_not_folded() -> None:
    records = [
        _decision(DAY, entity=MOTION, context=PERSON),  # a sensor: never noted
        _decision(DAY + _minutes(5), decision="act", target="Kitchen Pendants"),
    ]

    assert _verdicts(records)[1] == ("ask", None)


def test_a_record_with_an_outcome_is_judged_by_the_changes_it_recorded() -> None:
    shadow = ReflexOutcome(kind="shadow")
    records = [
        _decision(DAY, entity=BEDROOM, context=PERSON),  # the log shows a change to this lamp...
        _decision(
            DAY + _minutes(10),
            decision="act",
            target="Bedroom Lamp",
            outcome=shadow,
            recent={},  # ...but this record saw none, and is judged by what it saw
        ),
        _decision(
            DAY + _minutes(20),
            decision="act",
            target="Living Room Lamp",
            outcome=shadow,  # recorded as standing, though what it saw says otherwise
            recent={LAMP: _change(DAY + _minutes(15))},
        ),
    ]

    labelled = label_records(records, HOUSE, ReflexSettings(), "UTC")

    assert [(x.label, x.why) for x in labelled] == [
        ("none", "no_proposal"),
        ("ask", None),
        ("none", "reverses"),
    ]
    assert labelled[1].changes == {}
    assert set(labelled[2].changes) == {LAMP}


def test_the_recorded_outcome_is_never_trusted() -> None:
    record = _decision(
        DAY,
        decision="act",
        target="Kitchen Pendants",
        outcome=ReflexOutcome(kind="would_reverse", reversed=[PENDANTS]),
        recent={},
    )

    assert _verdicts([record]) == [("ask", None)]  # today's rule: nothing was changed


def test_a_fired_trigger_is_labelled_and_folds_nothing() -> None:
    records = [
        _fired(DAY, decision="act", target="Kitchen Pendants"),
        _fired(DAY + _minutes(5), decision="ask", target="Kitchen Pendants"),
    ]

    labelled = label_records(records, HOUSE, ReflexSettings(), "UTC")

    assert [(x.label, x.why) for x in labelled] == [("ask", None), ("none", "repeat")]
    assert labelled[0].changes == {}


def test_a_repeat_is_the_same_tool_and_targets_within_the_repeat_hours() -> None:
    two = [
        _decision(DAY, decision="act", target="Kitchen Pendants"),
        _decision(DAY + _minutes(10), decision="act", target="Kitchen Pendants"),
    ]
    by_area = [
        _decision(DAY, decision="act", target="Kitchen Pendants"),
        _decision(DAY + _minutes(10), decision="act", target=PENDANTS),  # the same light by id
    ]

    assert _verdicts(two)[1] == ("none", "repeat")
    assert _verdicts(by_area)[1] == ("none", "repeat")  # compared on what it resolves to
    assert _verdicts(two, ReflexSettings(repeat_hours=0))[1] == ("ask", None)  # none asked for
    assert _verdicts(two, ReflexSettings(repeat_hours=0.1))[1] == ("ask", None)  # 6 minutes
    assert _verdicts(two, ReflexSettings(repeat_hours=0.2))[1] == ("none", "repeat")  # 12


def test_a_record_that_is_not_an_ask_does_not_start_a_repeat_window() -> None:
    records = [
        _decision(DAY, decision="act", target="Garage Light"),  # unresolved: labelled none
        _decision(DAY + _minutes(5), decision="act", target="Kitchen Pendants"),
    ]

    assert _verdicts(records) == [("none", "unresolved"), ("ask", None)]


def test_quiet_hours_are_the_owners_local_time() -> None:
    record = _decision(
        datetime(2026, 10, 27, 13, 0, tzinfo=UTC), decision="act", target="Kitchen Pendants"
    )

    assert _verdicts([record], tz_name="UTC") == [("ask", None)]
    assert _verdicts([record], tz_name="America/Los_Angeles") == [("none", "quiet_hours")]  # 06:00


def test_the_why_is_the_first_rule_that_failed() -> None:
    late = DAY + _minutes(570)  # 23:30, in quiet hours
    records = [
        _decision(late, entity=BEDROOM, context=PERSON),
        _decision(late + _minutes(5), decision="act", target="Bedroom Lamp"),  # both rules fail
        _decision(late + _minutes(6), decision="act", target="Garage Light"),
    ]

    assert _verdicts(records)[1:] == [("none", "reverses"), ("none", "unresolved")]


def test_an_act_with_no_action_names_nothing_to_ask_about() -> None:
    assert _verdicts([_decision(DAY, decision="act")]) == [("none", "no_proposal")]


# --- labels: the command ------------------------------------------------------------------


async def test_label_writes_a_private_file_and_prints_counts_only(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _log()
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
            _decision(DAY - _minutes(90), decision="act", target="Kitchen Pendants"),  # too early
            *_log()[:3],
            _decision(DAY + _minutes(300), decision="act", target="Kitchen Pendants"),  # at until
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
    asked_at = DAY + _minutes(360)
    records = _jsonl(
        tmp_path / "records.jsonl",
        [_decision(asked_at, decision="act", target="Kitchen Pendants")],
    )
    out = tmp_path / "labels.jsonl"

    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(out)]))

    assert json.loads(out.read_text())["why"] == "quiet_hours"
    assert "Labelled 1 records: 0 ask, 1 none." in capsys.readouterr().out


async def test_label_drops_an_event_recorded_twice(
    house: AioRedis, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _decision(DAY, decision="act", target="Kitchen Pendants")
    records = _jsonl(tmp_path / "records.jsonl", [first, _again(first, seconds=30)])
    out = tmp_path / "labels.jsonl"

    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(out)]))

    shown = capsys.readouterr().out
    assert "Dropped 1 duplicate record." in shown
    assert "Labelled 1 records: 1 ask, 0 none." in shown
    assert len(out.read_text().splitlines()) == 1


async def test_label_without_live_state_exits_one_and_writes_nothing(
    server: fakeredis.FakeServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _jsonl(tmp_path / "records.jsonl", _log())
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
    path.write_text(f'{_log()[0].model_dump_json()}\n{{"prompt": "Kitchen Pendants"}}\n')

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
        _labelled("none", _decision(DAY), "no_proposal"),  # the model will act: a wrong ask
        _labelled("ask", _decision(DAY + _minutes(1), **ask)),  # the model will say none: missed
        _labelled("ask", _decision(DAY + _minutes(2), **ask)),  # it will ask about another light
        _labelled("ask", _decision(DAY + _minutes(3), **ask)),  # it will ask the same: matched
        _labelled(  # it will act on a lamp a person just changed
            "none",
            _decision(DAY + _minutes(4), decision="act", target="Living Room Lamp"),
            "reverses",
            {LAMP: _change(DAY + _minutes(1))},
        ),
        _labelled("ask", _decision(DAY + _minutes(5), **ask)),  # no decision: invalid
        _labelled("ask", _decision(DAY + _minutes(6), **ask)),  # a light there is not: invalid
        _labelled("none", _decision(DAY + _minutes(7)), "no_proposal"),  # none and none: right
        _labelled("ask", _decision(DAY + _minutes(8), **ask)),  # the call fails
    ]
    return lines


def _scored_replies() -> list[str | BaseException]:
    return [
        _act("Living Room Lamp"),
        NONE,
        _act("Living Room Lamp"),
        _act("Kitchen Pendants"),
        _act("Living Room Lamp"),
        "this is not a decision",
        _act("Garage Light"),
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
    assert "Wrong asks: 1 of 3 labelled none (33.3%)" in shown
    assert "Missed asks: 1 of 5 labelled ask (20.0%)" in shown
    assert "Other asks: 1 of 5 labelled ask (20.0%)" in shown
    assert "Matched asks: 1 of 5 labelled ask (20.0%)" in shown
    assert "Would reverse a person: 1 (must be 0)" in shown
    assert "Invalid replies: 2 of 8 (25.0%)" in shown
    assert "Model latency: p50" in shown and "p95" in shown
    assert "Failed calls: RuntimeError x1" in shown


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
    use_script(_act("Living Room Lamp"), _act("Living Room Lamp"))
    record = _decision(DAY, decision="act", target="Kitchen Pendants")
    labels = _label_file(
        tmp_path / "labels.jsonl",
        [
            _labelled("ask", record),  # no change: the lamp is fair game
            _labelled("none", record, "reverses", {LAMP: _change(DAY - _minutes(5))}),
        ],
    )

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr().out
    assert "Other asks: 1 of 1 labelled ask" in shown
    assert "Would reverse a person: 1 (must be 0)" in shown
    assert "Wrong asks: 0 of 1 labelled none" in shown


async def test_a_candidate_that_would_reverse_a_person_does_not_change_the_exit_status(
    house: AioRedis, use_script: Callable[..., Script], tmp_path: Path
) -> None:
    use_script(_act("Living Room Lamp"))
    record = _decision(DAY, decision="act", target="Living Room Lamp")
    labels = _label_file(
        tmp_path / "labels.jsonl",
        [_labelled("none", record, "reverses", {LAMP: _change(DAY - _minutes(5))})],
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
        _labelled("ask", _decision(DAY + _minutes(i), decision="act", target="Kitchen Pendants"))
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
    good = _labelled("none", _decision(DAY), "no_proposal").model_dump_json()
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
    records = _jsonl(tmp_path / "records.jsonl", _log())
    labels = tmp_path / "labels.jsonl"
    await replay.execute(parse_args(["label", "--records", str(records), "--out", str(labels)]))
    capsys.readouterr()
    use_script(*[NONE] * 9)

    await replay.execute(parse_args(["score", "--labels", str(labels)]))

    shown = capsys.readouterr().out
    assert "Scored 9 of 9 records, 0 failed." in shown
    assert "Missed asks: 3 of 3 labelled ask (100.0%)" in shown
