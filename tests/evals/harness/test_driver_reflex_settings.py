"""The reflex_settings step: the owner changes Reflex's settings through the admin API, the
sample puts back what it first found, and an ask Reflex parked is swept like any pending action."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from evals.harness.driver import HarnessError, play
from evals.harness.scenario import expand_variants
from tests.evals.harness.factories import FakeBus
from tests.evals.harness.test_checks_pending import pending_action
from tests.evals.harness.test_driver import (
    LAMP_OFF,
    WATCHES,
    Recorder,
    ctx,
    played,
    scenario,
    timed_play,
)

if TYPE_CHECKING:
    from bus.schemas.events import AlfredResponse, UserRequest

ASK = {"reflex_settings": {"mode": "ask", "ask_tools": ["home.light_*"]}}
ASKING = {"mode": "ask", "ask_tools": ["home.light_*"]}
SHADOW = {"mode": "shadow", "ask_tools": []}


async def test_the_step_changes_the_settings_and_the_sample_puts_back_what_it_found() -> None:
    bus = FakeBus()
    ev = await played(ctx(Recorder(), bus=bus), steps=[ASK, {"user": "Hello."}])
    assert bus.settings_log == [("set", ASKING), ("restore", SHADOW)]
    assert bus.reflex_settings == SHADOW
    assert ev.step_kinds == ["reflex_settings", "user"]
    assert "reflex settings changed" in ev.transcript[0].text


async def test_a_later_change_does_not_move_what_the_sample_restores() -> None:
    bus = FakeBus()
    bus.reflex_settings = {"mode": "shadow", "ask_tools": ["home.cover_*"], "daily_ask_cap": 3}
    later = {"reflex_settings": {"mode": "shadow"}}
    await played(ctx(Recorder(), bus=bus), steps=[ASK, later, {"user": "Hello."}])
    assert bus.reflex_settings == {
        "mode": "shadow",
        "ask_tools": ["home.cover_*"],
        "daily_ask_cap": 3,
    }
    assert bus.settings_log[-1][0] == "restore"
    assert [kind for kind, _ in bus.settings_log] == ["set", "set", "restore"]


async def test_a_sample_that_changed_no_settings_restores_none() -> None:
    bus = FakeBus()
    await played(ctx(Recorder(), bus=bus), steps=[{"user": "Hello."}])
    assert bus.settings_log == []


async def test_a_failed_sample_leaves_the_restore_to_the_stack_restart() -> None:
    """As for do-not-disturb: a failed sample dirties the container, and the next sample
    boots a fresh one with the stored defaults."""
    bus = FakeBus()

    async def fail(request: UserRequest, timeout: float) -> AlfredResponse:
        raise HarnessError("no reply")

    with pytest.raises(HarnessError):
        await played(ctx(fail, bus=bus), steps=[ASK, {"user": "Hello."}])
    assert bus.settings_log == [("set", ASKING)]


async def test_a_reflex_golden_starts_with_no_ask_left_by_the_last_one() -> None:
    """An open ask holds the next ask, and a repeat key holds it for hours: a golden that
    watches Reflex clears both, and one that does not has no need to."""
    bus = FakeBus()
    play_ctx = ctx(Recorder(), bus=bus, reflex_cooldown_s=0, reflex_timeout_s=0)
    await timed_play(play_ctx, scenario(**WATCHES))
    assert bus.asks_cleared == 1
    await timed_play(play_ctx, scenario(steps=[LAMP_OFF]))  # it does not watch Reflex
    assert bus.asks_cleared == 1


async def test_a_pending_action_reflex_parked_is_swept_and_a_foreign_one_is_kept() -> None:
    """The driver owns every pending action that was not there when the sample began, whoever
    parked it, so the ask Reflex parks for a golden does not outlive the golden."""
    bus = FakeBus()
    bus.pending = [pending_action("keep")]
    ask = pending_action(
        "ask-1",
        source="reflex-engine",
        tool_name="home.light_turn_on",
        parameters={"target": "living_room"},
    )

    async def park(request: UserRequest, timeout: float) -> AlfredResponse:
        bus.pending.append(ask)
        return await Recorder()(request, timeout)

    play_ctx = ctx(park, bus=bus, reflex_cooldown_s=0, reflex_timeout_s=0)
    [variant] = expand_variants(
        scenario(steps=[{"user": "Hello."}, LAMP_OFF, {"wait": 0.001}], expect=WATCHES["expect"])
    )
    ev = await play(play_ctx, variant, epoch=1)
    assert [a.request_id for a in ev.pending[-1].actions] == ["ask-1"]  # seen, for the check
    assert ev.pending[-1].actions[0].source == "reflex-engine"
    assert [a.request_id for a in bus.pending] == ["keep"]  # swept
