"""World fixtures: the home the fake Home Assistant serves (evals/harness/worlds/).

YAML gotcha: a bare ``on``/``off`` is a boolean. HA states must be quoted
(``state: "on"``); ``WorldEntity.state`` is a str and rejects booleans, so the mistake
fails loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.evidence import HaState

WORLDS_DIR = Path(__file__).resolve().parent / "worlds"


class WorldArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area_id: str
    name: str


class WorldDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    area_id: str | None = None


class WorldEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    name: str
    area_id: str | None = None
    device_id: str | None = None
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


def _number_selectors_are_numbers(services: dict[str, dict[str, dict[str, Any]]]) -> None:
    """The service fields' number selectors are what the fake HA range-checks calls
    against; a malformed one fails here, at load, not as a crash at call time."""
    for domain, by_service in services.items():
        for service, spec in by_service.items():
            fields = spec.get("fields") or {}
            where = f"services.{domain}.{service}.fields"
            if not isinstance(fields, dict):
                raise ValueError(f"{where} must be a mapping, got {fields!r}")
            for key, field in fields.items():
                if field is None:
                    continue
                if not isinstance(field, dict):
                    raise ValueError(f"{where}.{key} must be a mapping, got {field!r}")
                selector = field.get("selector")
                if selector is None:
                    continue
                if not isinstance(selector, dict):
                    raise ValueError(f"{where}.{key}.selector must be a mapping, got {selector!r}")
                number = selector.get("number")
                if number is None:
                    continue
                if not isinstance(number, dict):
                    raise ValueError(
                        f"{where}.{key}.selector.number must be a mapping, got {number!r}"
                    )
                for bound in ("min", "max"):
                    value = number.get(bound)
                    if value is not None and (
                        isinstance(value, bool) or not isinstance(value, int | float)
                    ):
                        raise ValueError(
                            f"{where}.{key}.selector.number.{bound} must be a number, got {value!r}"
                        )


class World(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    areas: list[WorldArea]
    devices: list[WorldDevice] = Field(default_factory=list)
    entities: list[WorldEntity]
    services: dict[str, dict[str, dict[str, Any]]]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        areas = {a.area_id for a in self.areas}
        devices = {d.id for d in self.devices}
        ids = [e.entity_id for e in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate entity ids")
        for e in self.entities:
            if e.area_id is not None and e.area_id not in areas:
                raise ValueError(f"{e.entity_id}: unknown area {e.area_id}")
            if e.device_id is not None and e.device_id not in devices:
                raise ValueError(f"{e.entity_id}: unknown device {e.device_id}")
        _number_selectors_are_numbers(self.services)
        return self

    def initial_states(self) -> dict[str, HaState]:
        return {
            e.entity_id: HaState(
                state=e.state, attributes={"friendly_name": e.name, **e.attributes}
            )
            for e in self.entities
            if not e.disabled
        }

    def entity_registry(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": e.entity_id,
                "area_id": e.area_id,
                "device_id": e.device_id,
                "name": None,
                "original_name": e.name,
                "disabled_by": "user" if e.disabled else None,
            }
            for e in self.entities
        ]

    def device_registry(self) -> list[dict[str, Any]]:
        return [
            {"id": d.id, "area_id": d.area_id, "name": d.name, "name_by_user": None}
            for d in self.devices
        ]

    def area_registry(self) -> list[dict[str, Any]]:
        return [{"area_id": a.area_id, "name": a.name} for a in self.areas]

    def area_of(self, entity_id: str) -> str | None:
        return next((e.area_id for e in self.entities if e.entity_id == entity_id), None)

    def entity_ids_in_area(self, area_id: str, domain: str) -> list[str]:
        return sorted(
            e.entity_id
            for e in self.entities
            if e.area_id == area_id and e.domain == domain and not e.disabled
        )


def load_world(name: str, root: Path = WORLDS_DIR) -> World:
    path = root / f"{name}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in root.glob("*.yaml"))
        raise FileNotFoundError(f"no world {name!r} in {root}; known: {known}")
    return World.model_validate(yaml.safe_load(path.read_text()))
