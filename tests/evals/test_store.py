"""Tests for evals.store — save/load EvalRun as JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bus.schemas.events import StateChangedEvent
from evals.models import EvalRun, Scenario, ScenarioResult, Verdict
from evals.store import build_run_id, list_runs, load_run, save_run
from shared.tracing import TraceRecord

if TYPE_CHECKING:
    import pathlib


def _make_run() -> EvalRun:
    event = StateChangedEvent(source="eval", domain="home", entity_id="light.lr", new_state="on")
    trace = TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test-model",
        event=event,
        preferences_text="prefs",
        tools=[],
        prompt="prompt",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )
    result = ScenarioResult(
        scenario=Scenario(name="s1", event=event, expected=None),
        verdict=Verdict.PASS,
        reason="correctly took no action",
        trace=trace,
    )
    return EvalRun(
        run_id="2026-03-10T143000_test-model",
        timestamp=datetime.now(UTC),
        model="test-model",
        results=[result],
    )


def test_save_and_load_round_trip(tmp_path: pathlib.Path) -> None:
    run = _make_run()
    save_run(run, tmp_path)
    loaded = load_run(run.run_id, tmp_path)
    assert loaded.run_id == run.run_id
    assert loaded.summary[Verdict.PASS] == 1
    assert len(loaded.results) == 1


def test_list_runs(tmp_path: pathlib.Path) -> None:
    run = _make_run()
    save_run(run, tmp_path)
    runs = list_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0] == run.run_id


def test_build_run_id() -> None:
    ts = datetime(2026, 3, 10, 14, 30, 0, tzinfo=UTC)
    run_id = build_run_id(ts, "gpt-oss:20b")
    assert run_id == "2026-03-10T143000_gpt-oss-20b"


def test_build_run_id_sanitizes_colons() -> None:
    ts = datetime(2026, 3, 10, 14, 30, 0, tzinfo=UTC)
    run_id = build_run_id(ts, "model:with:colons")
    assert ":" not in run_id
