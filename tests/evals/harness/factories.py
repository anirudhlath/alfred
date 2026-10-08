from __future__ import annotations

from typing import TYPE_CHECKING, Any

from evals.harness.evidence import Evidence, HaCall, HaState, LlmCall, Reply, ToolCall

if TYPE_CHECKING:
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
