"""Checks over what the fake Home Assistant saw and holds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.harness.checks.matching import describe, validate_fields, value_matches
from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, HaCall

EntityIds = str | list[str]


def _ids(value: EntityIds | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class HaWindowParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after_step: int | None = None
    at_step: int | None = None

    @model_validator(mode="after")
    def _one_window(self) -> Self:
        if self.at_step is not None and self.after_step is not None:
            raise ValueError("at_step and after_step cannot be combined")
        return self


class HaCalledParams(HaWindowParams):
    model_config = ConfigDict(extra="forbid")
    domain: str
    service: str
    entity_id: EntityIds | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    count: int | None = Field(default=None, ge=0)

    @field_validator("data")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_fields(value)


class HaNotCalledParams(HaWindowParams):
    model_config = ConfigDict(extra="forbid")
    domain: str | None = None
    service: str | None = None
    entity_id: EntityIds | None = None


class HaStateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    state: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_fields(value)


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


def satisfies(p: HaCalledParams, call: HaCall) -> bool:
    """Whether *call* is one an ``ha_called`` check with *p* looks for (its step range
    aside): the driver waits on this after an ``ha_event``."""
    return _matches(call, p.domain, p.service, _ids(p.entity_id), p.data)


def _fmt(calls: list[HaCall]) -> str:
    return (
        "; ".join(
            f"{c.domain}.{c.service} {c.entity_ids} {describe(c.service_data)}" for c in calls
        )
        or "none"
    )


def ha_called(evidence: Evidence, p: HaCalledParams) -> CheckResult:
    calls = _calls(evidence, p)
    want = f"{p.domain}.{p.service} {_ids(p.entity_id)} {describe(p.data)}"
    count = sum(satisfies(p, c) for c in calls)
    if (p.count is None and count > 0) or count == p.count:
        return passed("ha_called", f"saw {want}")
    expected = "at least one" if p.count is None else str(p.count)
    return failed("ha_called", f"wanted {expected} {want}; saw {count}; HA calls: {_fmt(calls)}")


def _calls(evidence: Evidence, p: HaWindowParams) -> list[HaCall]:
    if p.at_step is not None:
        return evidence.calls_at_step(p.at_step)
    return evidence.calls_after_step(p.after_step)


def ha_not_called(evidence: Evidence, p: HaNotCalledParams) -> CheckResult:
    bad = [
        c for c in _calls(evidence, p) if _matches(c, p.domain, p.service, _ids(p.entity_id), {})
    ]
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
