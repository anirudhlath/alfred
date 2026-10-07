"""Goldens: one YAML file per golden under evals/suites/<suite>/ (docs/evals.md).

YAML gotcha: a bare ``on``/``off`` is a boolean. HA states must be quoted
(``state: "on"``); the str fields here reject booleans, so the mistake fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from evals.harness.checks import CHECK_PARAMS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

SUITES_DIR = Path(__file__).resolve().parent.parent / "suites"

Who = Literal["sir", "guest"]
Channel = Literal["web_pwa", "signal", "voice", "ios", "satellite"]
_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


class ScenarioError(ValueError):
    """A golden failed to load. The message starts with the file path."""


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    who: Who = "sir"
    channel: Channel = "web_pwa"
    tz: str | None = "America/Denver"


class UserStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    user: str = Field(min_length=1)
    variants: list[str] = Field(default_factory=list)
    actor: Actor | None = Field(default=None, alias="as")


class HaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class HaEventStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ha_event: HaEvent
    settle: float = Field(default=3.0, ge=0)


class WaitStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wait: float = Field(gt=0, le=600)


Step = UserStep | HaEventStep | WaitStep
_STEP_TYPES: dict[str, type[BaseModel]] = {
    "user": UserStep,
    "ha_event": HaEventStep,
    "wait": WaitStep,
}


class CheckSpec(BaseModel):
    """One ``expect`` entry: a single-key mapping ``{check_name: params}``."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    name: str
    params: Any

    @model_validator(mode="before")
    @classmethod
    def _from_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict) and set(data) != {"name", "params"}:
            if len(data) != 1:
                raise ValueError(f"a check is a single-key mapping, got keys {sorted(data)}")
            ((name, params),) = data.items()
            if name not in CHECK_PARAMS:
                raise ValueError(f"unknown check {name!r}; known: {sorted(CHECK_PARAMS)}")
            return {"name": name, "params": CHECK_PARAMS[name].model_validate(params or {})}
        return data


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    prd: list[str] = Field(min_length=1)
    status: Literal["shipped", "pending"]
    tags: list[str] = Field(default_factory=list)
    world: str = "apartment"
    isolated: bool = False
    actor: Actor = Field(default_factory=Actor, alias="as")
    steps: list[Step] = Field(min_length=1)
    expect: list[CheckSpec] = Field(min_length=1)
    suite: str = ""
    path: str = ""

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError(f"id {value!r} must look like suite.topic.case (lowercase, dots)")
        return value

    @field_validator("steps", mode="before")
    @classmethod
    def _typed_steps(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        typed: list[BaseModel] = []
        for i, raw in enumerate(value):
            keys = set(raw) if isinstance(raw, dict) else set()
            kinds = keys & set(_STEP_TYPES)
            if len(kinds) != 1:
                raise ValueError(f"step {i}: needs one of user, ha_event, wait; got {sorted(keys)}")
            typed.append(_STEP_TYPES[kinds.pop()].model_validate(raw))
        return typed

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        with_variants = [s for s in self.steps if isinstance(s, UserStep) and s.variants]
        if len(with_variants) > 1:
            raise ValueError("only one step may have variants")
        needs_reply = {"judge", "reply_contains", "reply_not_contains", "latency"}
        if any(c.name in needs_reply for c in self.expect) and not any(
            isinstance(s, UserStep) for s in self.steps
        ):
            raise ValueError("reply and judge checks need at least one user step")
        return self


class ScenarioVariant(BaseModel):
    scenario: Scenario
    variant: int
    steps: list[Step]

    @property
    def sample_id(self) -> str:
        return self.scenario.id if self.variant == 0 else f"{self.scenario.id}~{self.variant}"


def expand_variants(scenario: Scenario) -> list[ScenarioVariant]:
    base: list[Step] = [
        s.model_copy(update={"variants": []}) if isinstance(s, UserStep) else s
        for s in scenario.steps
    ]
    out = [ScenarioVariant(scenario=scenario, variant=0, steps=base)]
    for index, step in enumerate(scenario.steps):
        if isinstance(step, UserStep) and step.variants:
            for n, text in enumerate(step.variants, start=1):
                steps = list(base)
                steps[index] = base[index].model_copy(update={"user": text})
                out.append(ScenarioVariant(scenario=scenario, variant=n, steps=steps))
    return out


def load_scenario(path: Path, suite: str) -> Scenario:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping at the top level")
    try:
        scenario = Scenario.model_validate({**raw, "suite": suite, "path": str(path)})
    except ValidationError as exc:
        raise ScenarioError(f"{path}: {exc}") from exc
    if not scenario.id.startswith(f"{suite}."):
        raise ScenarioError(f"{path}: id {scenario.id!r} must start with '{suite}.'")
    return scenario


def available_suites(root: Path = SUITES_DIR) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("*.yaml")))


def load_suites(
    names: Sequence[str] | None = None, root: Path = SUITES_DIR
) -> dict[str, list[Scenario]]:
    known = available_suites(root)
    wanted = list(names) if names else known
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise ScenarioError(f"unknown suite(s) {unknown}; available: {', '.join(known)}")
    seen: dict[str, str] = {}
    out: dict[str, list[Scenario]] = {}
    for name in wanted:
        scenarios = [load_scenario(p, name) for p in sorted((root / name).glob("*.yaml"))]
        for s in scenarios:
            if s.id in seen:
                raise ScenarioError(f"{s.path}: duplicate id {s.id!r} (also in {seen[s.id]})")
            seen[s.id] = s.path
        out[name] = scenarios
    return out


def select(
    scenarios: Iterable[Scenario], *, tags: Sequence[str] = (), include_pending: bool = False
) -> list[Scenario]:
    return [
        s
        for s in scenarios
        if (include_pending or s.status == "shipped") and (not tags or set(tags) & set(s.tags))
    ]
