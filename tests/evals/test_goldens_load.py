from __future__ import annotations

from typing import Any

from evals.harness.checks.home import HaCalledParams
from evals.harness.checks.latency import LatencyParams
from evals.harness.checks.reply import ReplyTextParams
from evals.harness.scenario import HaEventStep, Scenario, UserStep, load_suites
from evals.harness.world import load_world

SUITES = ["conversation", "home_control"]


def test_every_golden_loads_and_names_real_entities() -> None:
    world_ids = {e.entity_id for e in load_world("apartment").entities}
    suites = load_suites(SUITES)
    assert len(suites["conversation"]) >= 8 and len(suites["home_control"]) >= 13
    for scenarios in suites.values():
        for s in scenarios:
            for step in s.steps:
                if isinstance(step, HaEventStep):
                    assert step.ha_event.entity_id in world_ids, s.path
            for check in s.expect:
                entity = getattr(check.params, "entity_id", None)
                for e in [entity] if isinstance(entity, str) else (entity or []):
                    assert e in world_ids, f"{s.path}: {e}"


def _bad_step_indexes(s: Scenario) -> list[str]:
    """Step indexes in ``s``'s checks that point past its steps.

    The loader does not check these; a bad one only errors in a live run.

    - ``step`` on a reply or latency check indexes ``Evidence.replies``, which holds one
      reply per *user* step (``checks/reply.py``, ``checks/latency.py``).
    - ``after_step`` on ``ha_called`` indexes ``Evidence.step_started``, which the driver
      appends to for *every* step — user, ha_event and wait alike (``driver.play``). So it
      counts all steps, and ``calls_after_step`` keeps the HA calls made from that step's
      start onward.

    Both are plain Python list indexes, so a negative one counts from the end.
    """
    users = sum(isinstance(step, UserStep) for step in s.steps)
    steps = len(s.steps)
    bad: list[str] = []
    for check in s.expect:
        p = check.params
        reply_step = p.step if isinstance(p, ReplyTextParams | LatencyParams) else None
        if isinstance(reply_step, int) and not -users <= reply_step < users:
            bad.append(f"{check.name}.step={reply_step} but {users} user step(s)")
        after = p.after_step if isinstance(p, HaCalledParams) else None
        if after is not None and not -steps <= after < steps:
            bad.append(f"{check.name}.after_step={after} but {steps} step(s)")
    return bad


def test_every_golden_step_index_names_a_real_step() -> None:
    for scenarios in load_suites(SUITES).values():
        for s in scenarios:
            assert not _bad_step_indexes(s), f"{s.path}: {_bad_step_indexes(s)}"


def _scenario(steps: list[dict[str, Any]], expect: list[dict[str, Any]]) -> Scenario:
    golden = {"id": "home_control.t.t", "prd": ["x"], "status": "shipped"}
    return Scenario.model_validate({**golden, "steps": steps, "expect": expect})


def test_bad_step_indexes_counts_user_steps_for_replies_and_all_steps_for_after_step() -> None:
    event = {"ha_event": {"entity_id": "light.bedroom_lamp", "state": "on"}}
    steps: list[dict[str, Any]] = [event, {"user": "a"}, {"user": "b"}]  # 2 of 3 are user steps
    ok = _scenario(
        steps,
        [
            {"reply_contains": {"text": "x", "step": 1}},
            {"reply_not_contains": {"text": "x", "step": -2}},
            {"reply_contains": {"text": "x", "step": "any"}},
            {"latency": {"metric": "reply_ms", "max": 1, "step": 0}},
            {"ha_called": {"domain": "light", "service": "turn_on", "after_step": 2}},
            {"ha_called": {"domain": "light", "service": "turn_on", "after_step": -3}},
        ],
    )
    assert _bad_step_indexes(ok) == []
    bad = _scenario(
        steps,
        [
            {"reply_contains": {"text": "x", "step": 2}},
            {"reply_not_contains": {"text": "x", "step": -3}},
            {"latency": {"metric": "reply_ms", "max": 1, "step": 2}},
            {"ha_called": {"domain": "light", "service": "turn_on", "after_step": 3}},
            {"ha_called": {"domain": "light", "service": "turn_on", "after_step": -4}},
        ],
    )
    assert len(_bad_step_indexes(bad)) == 5
