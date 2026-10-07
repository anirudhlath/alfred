from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from evals.harness.scenario import CheckSpec, HaEventStep, Scenario, UserStep, load_suites
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


def _step_indexes(s: Scenario) -> list[tuple[str, object, int]]:
    """``(check.field, value, n)`` for every step index ``s``'s checks carry.

    The loader does not check these, so a bad one would only error in a live run. Any check
    whose params model has a field with one of these names is covered, including checks
    added later:

    - ``step`` (reply and latency checks today) indexes ``Evidence.replies``, which holds
      one reply per *user* step (``checks/reply.py``, ``checks/latency.py``), so n counts
      user steps. ``"any"`` means every reply.
    - ``after_step`` (``ha_called`` today) indexes ``Evidence.step_started``, which the
      driver appends to for *every* step, whether user, ha_event or wait (``driver.play``),
      so n counts all steps. ``calls_after_step`` keeps the HA calls made from that step's
      start onward. ``None`` means no bound.

    Both are plain Python list indexes, so a valid index lies in ``-n <= i < n``.
    """
    users = sum(isinstance(step, UserStep) for step in s.steps)
    spaces = {"step": users, "after_step": len(s.steps)}
    out: list[tuple[str, object, int]] = []
    for check in s.expect:
        for field in spaces.keys() & type(check.params).model_fields.keys():
            value = getattr(check.params, field)
            if value is not None and not (field == "step" and value == "any"):
                out.append((f"{check.name}.{field}", value, spaces[field]))
    return out


def _bad_step_indexes(s: Scenario) -> list[str]:
    return [
        f"{label}={value!r} but n={n}"
        for label, value, n in _step_indexes(s)
        if not (isinstance(value, int) and -n <= value < n)
    ]


def test_every_golden_step_index_names_a_real_step() -> None:
    seen: set[str] = set()
    for scenarios in load_suites(SUITES).values():
        for s in scenarios:
            assert not _bad_step_indexes(s), f"{s.path}: {_bad_step_indexes(s)}"
            seen |= {label.rsplit(".", 1)[1] for label, _, _ in _step_indexes(s)}
    # If a rename left the field lookup matching nothing, this test would pass vacuously.
    assert seen == {"step", "after_step"}


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


class _FutureParams(BaseModel):
    """A check added later that reuses the field names: the guard must cover it unasked."""

    step: int | str = -1
    after_step: int | None = None


def test_step_index_guard_covers_any_check_with_those_fields() -> None:
    golden = _scenario([{"user": "a"}], [{"reply_contains": {"text": "x"}}])  # 1 step, a user step

    def bad(**params: Any) -> list[str]:
        check = CheckSpec.model_construct(name="future", params=_FutureParams(**params))
        return _bad_step_indexes(golden.model_copy(update={"expect": [check]}))

    assert bad(step=0, after_step=-1) == []
    assert bad(step="any") == []
    assert bad(step=1) == ["future.step=1 but n=1"]
    assert bad(after_step=1) == ["future.after_step=1 but n=1"]
    assert bad(step="first") == ["future.step='first' but n=1"]
