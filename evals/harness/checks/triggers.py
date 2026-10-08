"""Checks over the triggers System 2 created in the sample, and their fires.

A trigger's ``conditions`` are as the engine normalised them: a relative delay is
already a ``run_at`` in the user's zone, so ``run_in_seconds`` is measured from the
trigger's creation to that ``run_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.harness.checks.matching import describe, is_approx, validate_fields, value_matches
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Urgency  # noqa: TC001 — Pydantic resolves the params

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, TriggerRecord

TriggerType = Literal["time", "sensor", "composite"]


class AtLocal(BaseModel):
    """The trigger's ``run_at``, shown in ``tz``, reads ``time`` (``HH:MM``)."""

    model_config = ConfigDict(extra="forbid")
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    tz: str

    @field_validator("tz")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value


class TriggerCreatedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TriggerType | None = None
    name: str | None = None  # a substring of the trigger's name, case-insensitive
    conditions: dict[str, Any] = Field(default_factory=dict)  # {field: expected}
    run_in_seconds: float | dict[str, float] | None = None  # a number or {approx, tol}
    at_local: AtLocal | None = None
    urgency: Urgency | None = None
    one_shot: bool | None = None

    @field_validator("conditions")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_fields(value)

    @field_validator("run_in_seconds")
    @classmethod
    def _number_or_approx(cls, value: object) -> object:
        if isinstance(value, dict) and not is_approx(value):
            raise ValueError("run_in_seconds is a number or {approx, tol}")
        return value


class TriggerTypeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TriggerType | None = None


class TriggerFiredParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    after_step: int | None = None
    within_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _bound_needs_a_start(self) -> Self:
        if self.within_s is not None and self.after_step is None:
            raise ValueError("within_s counts from after_step's start: give after_step too")
        return self


def _run_at(r: TriggerRecord) -> datetime | None:
    raw = r.conditions.get("run_at")
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def _run_in(r: TriggerRecord) -> float | None:
    when = _run_at(r)
    return None if when is None else (when - r.created_at).total_seconds()


def _fits(r: TriggerRecord, p: TriggerCreatedParams) -> bool:
    if p.type is not None and r.trigger_type != p.type:
        return False
    if p.name is not None and p.name.lower() not in r.name.lower():
        return False
    if not all(
        k in r.conditions and value_matches(v, r.conditions[k]) for k, v in p.conditions.items()
    ):
        return False
    if p.run_in_seconds is not None:
        seconds = _run_in(r)
        if seconds is None or not value_matches(p.run_in_seconds, seconds):
            return False
    if p.at_local is not None:
        when = _run_at(r)
        local = None if when is None else f"{when.astimezone(ZoneInfo(p.at_local.tz)):%H:%M}"
        if local != p.at_local.time:
            return False
    if p.urgency is not None and r.urgency != p.urgency:
        return False
    return p.one_shot is None or r.one_shot is p.one_shot


def _describe(r: TriggerRecord) -> str:
    text = f"{r.trigger_type} {r.name!r} {describe(r.conditions)}"
    if (seconds := _run_in(r)) is not None:
        text += f" (in {seconds:.0f}s)"
    return f"{text} urgency={r.urgency}"


def _seen(records: list[TriggerRecord]) -> str:
    return "; ".join(_describe(r) for r in records) or "none"


def trigger_created(evidence: Evidence, p: TriggerCreatedParams) -> CheckResult:
    hits = [r for r in evidence.triggers_created if _fits(r, p)]
    if hits:
        return passed("trigger_created", _describe(hits[0]))
    want = describe(p.model_dump(exclude_none=True, exclude_defaults=True))
    return failed(
        "trigger_created", f"no trigger like {want}; created: {_seen(evidence.triggers_created)}"
    )


def trigger_not_created(evidence: Evidence, p: TriggerTypeParams) -> CheckResult:
    hits = [r for r in evidence.triggers_created if p.type is None or r.trigger_type == p.type]
    if hits:
        return failed("trigger_not_created", f"created {_seen(hits)}")
    kind = "" if p.type is None else f"{p.type} "
    return passed("trigger_not_created", f"no {kind}trigger created")


def trigger_fired(evidence: Evidence, p: TriggerFiredParams) -> CheckResult:
    """A fire of a trigger created in this sample: an earlier sample's trigger can still
    fire in this one's window, and is not this golden's evidence."""
    name = "trigger_fired"
    mine = {r.trigger_id for r in evidence.triggers_created}
    fires = [
        f
        for f in evidence.triggers_fired
        if f.trigger_id in mine and (p.name is None or p.name.lower() in f.name.lower())
    ]
    start: float | None = None
    if p.after_step is not None:
        start = evidence.step_started[evidence.step_index(p.after_step)]
        fires = [f for f in fires if f.t >= start]
    if not fires:
        since = "" if p.after_step is None else f" from step {p.after_step}"
        return failed(
            name,
            f"no trigger created in this sample fired{since} "
            f"(created: {_seen(evidence.triggers_created)})",
        )
    first = fires[0]
    if p.within_s is None or start is None:
        return passed(name, f"{first.name!r} fired")
    took = first.t - start
    if took > p.within_s:
        return failed(
            name,
            f"{first.name!r} fired {took:.1f}s after step {p.after_step}, bound {p.within_s:g}s",
        )
    return passed(name, f"{first.name!r} fired {took:.1f}s after step {p.after_step}")
