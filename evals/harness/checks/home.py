"""Checks over what the fake Home Assistant saw and holds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.checks.matching import describe, value_matches
from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, HaCall

EntityIds = str | list[str]


def _ids(value: EntityIds | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class HaCalledParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str
    service: str
    entity_id: EntityIds | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    after_step: int | None = None


class HaNotCalledParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str | None = None
    service: str | None = None
    entity_id: EntityIds | None = None


class HaStateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    state: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


def _matches(
    call: HaCall, domain: str | None, service: str | None, ids: list[str], data: dict[str, Any]
) -> bool:
    if domain is not None and call.domain != domain:
        return False
    if service is not None and call.service != service:
        return False
    if ids and not set(ids) <= set(call.entity_ids):
        return False
    return all(
        k in call.service_data and value_matches(v, call.service_data[k]) for k, v in data.items()
    )


def _fmt(calls: list[HaCall]) -> str:
    return (
        "; ".join(
            f"{c.domain}.{c.service} {c.entity_ids} {describe(c.service_data)}" for c in calls
        )
        or "none"
    )


def ha_called(evidence: Evidence, p: HaCalledParams) -> CheckResult:
    calls = evidence.calls_after_step(p.after_step)
    want = f"{p.domain}.{p.service} {_ids(p.entity_id)} {describe(p.data)}"
    if any(_matches(c, p.domain, p.service, _ids(p.entity_id), p.data) for c in calls):
        return passed("ha_called", f"saw {want}")
    return failed("ha_called", f"wanted {want}; HA calls: {_fmt(calls)}")


def ha_not_called(evidence: Evidence, p: HaNotCalledParams) -> CheckResult:
    bad = [c for c in evidence.ha_calls if _matches(c, p.domain, p.service, _ids(p.entity_id), {})]
    if bad:
        return failed("ha_not_called", f"unexpected HA calls: {_fmt(bad)}")
    return passed("ha_not_called", "no matching HA call")


def ha_state(evidence: Evidence, p: HaStateParams) -> CheckResult:
    state = evidence.ha_states.get(p.entity_id)
    if state is None:
        return failed("ha_state", f"{p.entity_id} is not in the fake HA")
    if p.state is not None and not value_matches(p.state, state.state):
        return failed("ha_state", f"{p.entity_id} is {state.state!r}, wanted {p.state!r}")
    for key, want in p.attributes.items():
        have = state.attributes.get(key)
        if not value_matches(want, have):
            return failed(
                "ha_state", f"{p.entity_id}.{key} is {describe(have)}, wanted {describe(want)}"
            )
    return passed("ha_state", f"{p.entity_id} is {state.state!r}")
