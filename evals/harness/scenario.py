"""Goldens: one YAML file per golden under evals/suites/<suite>/ (docs/evals.md).

YAML gotcha: a bare ``on``/``off`` is a boolean. HA states must be quoted
(``state: "on"``); the str fields here reject booleans, so the mistake fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SerializeAsAny,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from evals.harness.checks import CHECK_PARAMS, NEEDS_REPLY

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

SUITES_DIR = Path(__file__).resolve().parent.parent / "suites"
GOLDEN_SUFFIX = ".yaml"
_LOADER_KEYS = frozenset({"suite", "path"})

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
    variants: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
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


_STEP_KINDS = ("user", "ha_event", "wait")


def _step_kind(value: Any) -> str | None:
    """The step's tag: the one step key a mapping (or a step model's fields) carries."""
    if isinstance(value, BaseModel):
        keys = set(type(value).model_fields)
    elif isinstance(value, dict):
        keys = set(value)
    else:
        return None
    kinds = [k for k in _STEP_KINDS if k in keys]
    return kinds[0] if len(kinds) == 1 else None


# Tagged so a field error names its step: ``steps.1.ha_event.ha_event.state``.
Step = Annotated[
    Annotated[UserStep, Tag("user")]
    | Annotated[HaEventStep, Tag("ha_event")]
    | Annotated[WaitStep, Tag("wait")],
    Discriminator(
        _step_kind,
        custom_error_type="step_kind",
        custom_error_message="needs one of user, ha_event, wait",
    ),
]


class CheckSpec(BaseModel):
    """One ``expect`` entry: ``{check_name: params}`` in a golden, or ``{name, params}``.

    Both shapes go through the same validation, so a check name or params that would
    only fail after the container boots fail at load instead.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    params: SerializeAsAny[BaseModel]

    @model_validator(mode="before")
    @classmethod
    def _validated(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if set(data) == {"name", "params"}:
            name, params = data["name"], data["params"]
        elif len(data) == 1:
            ((name, params),) = data.items()
        else:
            raise ValueError(f"a check is a single-key mapping, got keys {sorted(data)}")
        if not isinstance(name, str) or name not in CHECK_PARAMS:
            raise ValueError(f"unknown check {name!r}; known: {sorted(CHECK_PARAMS)}")
        # An existing params instance of the right model comes back unchanged.
        return {"name": name, "params": CHECK_PARAMS[name].model_validate(params or {})}


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

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        with_variants = [s for s in self.steps if isinstance(s, UserStep) and s.variants]
        if len(with_variants) > 1:
            raise ValueError("only one step may have variants")
        if any(c.name in NEEDS_REPLY for c in self.expect) and not any(
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
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScenarioError(f"{path}: not UTF-8: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping at the top level")
    reserved = sorted(_LOADER_KEYS & set(raw))
    if reserved:
        raise ScenarioError(f"{path}: keys {reserved} are set by the loader, not by a golden")
    try:
        scenario = Scenario.model_validate({**raw, "suite": suite, "path": str(path)})
    except ValidationError as exc:
        raise ScenarioError(f"{path}: {exc}") from exc
    if not scenario.id.startswith(f"{suite}."):
        raise ScenarioError(f"{path}: id {scenario.id!r} must start with '{suite}.'")
    return scenario


def available_suites(root: Path = SUITES_DIR) -> list[str]:
    """Every non-empty directory under ``root``. ``load_suites`` rejects what is not a golden."""
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.iterdir()))


def _golden_paths(suite_dir: Path) -> list[Path]:
    """The suite's goldens. Anything else in the directory would be skipped, so it is an error."""
    paths = sorted(suite_dir.iterdir())
    for path in paths:
        if not (path.is_file() and path.suffix == GOLDEN_SUFFIX):
            raise ScenarioError(f"{path}: not a golden; a suite holds only *{GOLDEN_SUFFIX} files")
    return paths


def load_suites(
    names: Sequence[str] | None = None, root: Path = SUITES_DIR
) -> dict[str, list[Scenario]]:
    known = available_suites(root)
    wanted = list(dict.fromkeys(names)) if names else known
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise ScenarioError(f"unknown suite(s) {unknown}; available: {', '.join(known)}")
    seen: dict[str, str] = {}
    out: dict[str, list[Scenario]] = {}
    for name in wanted:
        scenarios = [load_scenario(p, name) for p in _golden_paths(root / name)]
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
