from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from bus.schemas.events import ActionRequest, TriggerCreated, TriggerFired
from core.notifications.schema import Notification, Urgency
from evals.harness.bus import Entry
from evals.harness.evidence import Evidence, HaCall, HaState, LlmCall, Reply, ToolCall

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from pydantic import BaseModel

    from core.reflex.tool_registry import ToolInfo
    from evals.harness.evidence import Role, TranscriptTurn


def evidence(
    *,
    replies: list[str] | None = None,
    transcript: list[TranscriptTurn] | None = None,
    ha_calls: list[HaCall] | None = None,
    ha_states: dict[str, HaState] | None = None,
    llm_calls: list[LlmCall] | None = None,
    step_started: list[float] | None = None,
    latencies: list[float] | None = None,
    **extra: Any,
) -> Evidence:
    texts = replies or []
    lat = latencies or [1000.0] * len(texts)
    return Evidence(
        scenario_id="suite.case",
        variant=0,
        epoch=1,
        session_id="eval-test",
        started_at=0.0,
        ended_at=100.0,
        step_started=step_started or [0.0],
        transcript=transcript or [],
        replies=[
            Reply(step=i, text=t, source="conscious-engine", latency_ms=lat[i])
            for i, t in enumerate(texts)
        ],
        ha_calls=ha_calls or [],
        ha_states=ha_states or {},
        llm_calls=llm_calls or [],
        **extra,
    )


def call(
    domain: str, service: str, ids: list[str], data: dict[str, Any] | None = None, t: float = 1.0
) -> HaCall:
    return HaCall(t=t, domain=domain, service=service, service_data=data or {}, entity_ids=ids)


def llm(role: Role, *calls: tuple[str, dict[str, Any]]) -> LlmCall:
    return LlmCall(
        t=1.0,
        role=role,
        latency_ms=10.0,
        status=200,
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in calls],
    )


class FakeBus:
    """An in-memory ``Bus``. ``advance_trigger`` fires the trigger at once, as the engine
    does with a due ``run_at``; with ``fires=False`` the engine never gets to it, and a
    test fires it with ``fire``."""

    def __init__(
        self, *, tz: str | None = None, tools: list[ToolInfo] | None = None, fires: bool = True
    ) -> None:
        self.entries: list[Entry] = []  # alfred:events
        self.requests: list[Entry] = []  # alfred:actions
        self.sent: list[Entry] = []  # the notification dispatch stream
        self.held: list[str] = []  # the deferred list
        self.tools = tools or []
        self.tz = tz
        self.tz_set: list[str | None] = []
        self.dnd: list[bool] = []
        self.cleared = 0
        self.advanced: list[str] = []
        self.deleted: list[str] = []
        self.fires = fires
        self._names: dict[str, str] = {}
        self._actions: dict[str, dict[str, Any]] = {}
        self._landed = asyncio.Event()  # set, then replaced, when an entry is appended

    def _append(self, stream: list[Entry], event: BaseModel) -> None:
        stream.append(Entry(wall=time.time(), data={"event": event.model_dump_json()}))
        landed, self._landed = self._landed, asyncio.Event()
        landed.set()

    def created(
        self,
        trigger_id: str = "t1",
        name: str = "Laundry reminder",
        conditions: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
    ) -> None:
        """System 2 created a time trigger, now, with *action* to run when it fires."""
        event = TriggerCreated(
            trigger_id=trigger_id,
            trigger_type="time",
            name=name,
            created_by="tool-call",
            conditions=conditions or {"run_at": "2026-10-08T20:00:00+00:00"},
            action=action,
        )
        self._names[trigger_id] = name
        if action is not None:
            self._actions[trigger_id] = action
        self._append(self.entries, event)

    def fire(self, trigger_id: str) -> None:
        """The engine fired the trigger, now. One with no action sends a TriggerFired and its
        notification; one with an action sends that action to alfred:actions instead."""
        name = self._names[trigger_id]
        if (action := self._actions.get(trigger_id)) is not None:
            self.request(ActionRequest(source="trigger-engine", **action))
            return
        fired = TriggerFired(trigger_id=trigger_id, trigger_name=name, trigger_type="time")
        self._append(self.entries, fired)
        self.notify(f"Trigger: {name}")

    def request(self, action: ActionRequest) -> None:
        """An ActionRequest lands on alfred:actions, now."""
        self._append(self.requests, action)

    def notify(self, title: str, urgency: str = "informational", wall: float | None = None) -> None:
        note = Notification(title=title, body="", urgency=Urgency(urgency), source="trigger-engine")
        stamp = time.time() if wall is None else wall
        self.sent.append(Entry(wall=stamp, data={"notification": note.model_dump_json()}))

    def hold(self, title: str) -> None:
        note = Notification(
            title=title, body="", urgency=Urgency.INFORMATIONAL, source="trigger-engine"
        )
        self.held.append(note.model_dump_json())

    async def events(self, since_wall: float) -> list[Entry]:
        return [e for e in self.entries if e.wall >= since_wall]

    async def actions(self, since_wall: float) -> list[Entry]:
        return [e for e in self.requests if e.wall >= since_wall]

    async def wait_for_event(
        self, since_wall: float, timeout_s: float, wanted: Callable[[Entry], bool]
    ) -> bool:
        def landed() -> bool:
            return any(e.wall >= since_wall and wanted(e) for e in self.entries + self.requests)

        try:
            async with asyncio.timeout(timeout_s):
                while not landed():
                    await self._landed.wait()
        except TimeoutError:
            return False
        return True

    async def notifications(self, since_wall: float) -> list[Entry]:
        return [e for e in self.sent if e.wall >= since_wall]

    async def deferred(self) -> list[str]:
        return list(self.held)

    async def reflex_tools(self) -> list[ToolInfo]:
        return list(self.tools)

    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool:
        if trigger_id not in self._names:
            return False
        self.advanced.append(trigger_id)
        if self.fires:
            self.fire(trigger_id)
            del self._names[trigger_id]  # a one-shot is deleted when it fires
        return True

    async def delete_triggers(self, trigger_ids: list[str]) -> None:
        self.deleted.extend(trigger_ids)

    async def user_timezone(self) -> str | None:
        return self.tz

    async def set_user_timezone(self, tz: str | None) -> None:
        self.tz = tz
        self.tz_set.append(tz)

    async def set_dnd(self, active: bool) -> None:
        self.dnd.append(active)

    async def clear_dnd(self) -> None:
        self.cleared += 1
