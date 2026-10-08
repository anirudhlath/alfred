"""Tests for AlfredClient dispatch: arguments validated and coerced by the tool's signature."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from sdk.alfred_sdk import ToolArgumentError
from sdk.alfred_sdk.client import AlfredClient
from sdk.alfred_sdk.feature import BaseFeature, ToolMeta, ToolParameter, tool


class _Speed(enum.Enum):
    SLOW = "slow"
    FAST = "fast"


class _Window(BaseModel):
    start: _dt.datetime
    label: str = ""


_UNSET = object()


class _PlanFeature(BaseFeature):
    """Tools that record every call, so a test can prove one never happened."""

    feature_name = "plan"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    @tool
    def schedule(
        self,
        at: _dt.datetime,
        speed: _Speed,
        window: _Window,
        count: int,
        note: str = "",
    ) -> str:
        """Schedule something."""
        self.calls.append(
            {"at": at, "speed": speed, "window": window, "count": count, "note": note}
        )
        return "scheduled"

    @tool
    async def schedule_async(self, at: _dt.datetime, count: int) -> str:
        """Schedule something, asynchronously."""
        self.calls.append({"at": at, "count": count})
        return "scheduled"

    @tool
    def go(self, x: Annotated[int, Field(ge=0)] = 5) -> int:
        """A constrained parameter with its default on the signature."""
        self.calls.append({"x": x})
        return x

    @tool
    def dim(self, level: int, hidden: Annotated[int, SkipJsonSchema()] = 3) -> None:
        """A parameter the schema hides."""
        self.calls.append({"level": level, "hidden": hidden})

    @tool
    def find(self, query: str, cursor: Any = _UNSET) -> None:
        """A sentinel default JSON cannot carry."""
        self.calls.append({"query": query, "cursor": cursor})

    @tool
    def odd(self, model_config: int, _private: str = "") -> None:
        """Parameter names that collide with BaseModel internals."""
        self.calls.append({"model_config": model_config, "_private": _private})


class _HandBuiltFeature(BaseFeature):
    """A tool built without a signature, like home-service's generated ones."""

    feature_name = "hand"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self.go = self._handle

    def _handle(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "went"

    def get_tools(self) -> list[ToolMeta]:
        return [
            ToolMeta(
                name="hand.go",
                description="Go somewhere.",
                parameters={"to": ToolParameter(type="int", required=True)},
            )
        ]


class _EnrichedFeature(_PlanFeature):
    """Adjusts a discovered tool the documented way, with `dataclasses.replace`."""

    feature_name = "enriched"

    def get_tools(self) -> list[ToolMeta]:
        return [
            dataclasses.replace(t, description=t.description + " Enriched.")
            for t in super().get_tools()
        ]


Dispatch = Callable[[AlfredClient, str, dict[str, Any]], Awaitable[Any]]


async def _dispatch_async(client: AlfredClient, method: str, params: dict[str, Any]) -> Any:
    return await client.dispatch(method, params)


async def _dispatch_sync(client: AlfredClient, method: str, params: dict[str, Any]) -> Any:
    return client.dispatch_sync(method, params)


both_dispatches = pytest.mark.parametrize(
    "dispatch",
    [pytest.param(_dispatch_async, id="async"), pytest.param(_dispatch_sync, id="sync")],
)


def _client(feature_class: type[BaseFeature]) -> tuple[AlfredClient, Any]:
    client = AlfredClient(service_name="test-svc")
    (feature,) = client.discover_features_from_classes([feature_class])
    return client, feature


_VALID_SCHEDULE: dict[str, Any] = {
    "at": "2026-10-08T07:30:00+00:00",
    "speed": "fast",
    "window": {"start": "2026-10-08T07:00:00Z", "label": "morning"},
    "count": 3,
}


# ── coercion ──


@both_dispatches
async def test_dispatch_coerces_arguments_to_their_declared_types(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    result = await dispatch(client, "plan.schedule", {**_VALID_SCHEDULE, "count": "5"})
    assert result == "scheduled"
    (call,) = feature.calls
    assert call["at"] == _dt.datetime(2026, 10, 8, 7, 30, tzinfo=_dt.UTC)
    assert call["speed"] is _Speed.FAST
    assert isinstance(call["window"], _Window)
    assert call["window"].start == _dt.datetime(2026, 10, 8, 7, 0, tzinfo=_dt.UTC)
    assert call["window"].label == "morning"
    # Lax mode: a numeric string becomes the int the signature declares.
    assert call["count"] == 5
    assert call["note"] == ""


async def test_async_tool_receives_coerced_arguments() -> None:
    client, feature = _client(_PlanFeature)
    result = await client.dispatch(
        "plan.schedule_async", {"at": "2026-10-08T07:30:00Z", "count": "2"}
    )
    assert result == "scheduled"
    assert feature.calls == [{"at": _dt.datetime(2026, 10, 8, 7, 30, tzinfo=_dt.UTC), "count": 2}]


# ── refusals ──


@both_dispatches
@pytest.mark.parametrize(
    ("params", "fields"),
    [
        pytest.param(
            {k: v for k, v in _VALID_SCHEDULE.items() if k != "count"}, ["count"], id="missing"
        ),
        pytest.param({**_VALID_SCHEDULE, "count": "many"}, ["count"], id="invalid"),
        pytest.param({**_VALID_SCHEDULE, "colour": "red"}, ["colour"], id="unexpected"),
        pytest.param(
            {**_VALID_SCHEDULE, "window": {"label": "x"}}, ["window.start"], id="nested-missing"
        ),
    ],
)
async def test_bad_arguments_raise_one_error_type_and_never_call_the_tool(
    dispatch: Dispatch, params: dict[str, Any], fields: list[str]
) -> None:
    client, feature = _client(_PlanFeature)
    with pytest.raises(ToolArgumentError) as excinfo:
        await dispatch(client, "plan.schedule", params)
    message = str(excinfo.value)
    assert "'plan.schedule'" in message
    for name in fields:
        assert f"{name}:" in message
    assert excinfo.value.tool_name == "plan.schedule"
    assert [path for path, _ in excinfo.value.problems] == fields
    assert feature.calls == []


@both_dispatches
async def test_every_failing_field_is_named_by_its_parameter_name(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    params = {"at": "not a date", "speed": "warp", "window": {"start": "2026-10-08"}, "zzz": 1}
    with pytest.raises(ToolArgumentError) as excinfo:
        await dispatch(client, "plan.schedule", params)
    assert [path for path, _ in excinfo.value.problems] == ["at", "speed", "count", "zzz"]
    message = str(excinfo.value)
    # The model's own aliases (`p0`, `p1`, ...) never leak into what a caller sees.
    assert not any(f"p{i}" in message for i in range(5))
    assert "unexpected argument" in message.lower()
    assert feature.calls == []


@both_dispatches
async def test_arguments_that_are_not_an_object_are_refused(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    with pytest.raises(ToolArgumentError, match=r"'plan\.go'"):
        await dispatch(client, "plan.go", ["5"])  # type: ignore[arg-type]
    assert feature.calls == []


def test_tool_argument_error_is_a_value_error() -> None:
    assert issubclass(ToolArgumentError, ValueError)


# ── defaults and hidden parameters ──


@both_dispatches
async def test_an_omitted_argument_takes_the_signature_default(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    assert await dispatch(client, "plan.go", {}) == 5
    assert feature.calls == [{"x": 5}]


@both_dispatches
async def test_annotated_constraints_are_enforced(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    with pytest.raises(ToolArgumentError, match=r"x: Input should be greater than or equal to 0"):
        await dispatch(client, "plan.go", {"x": -1})
    assert feature.calls == []


@both_dispatches
async def test_a_sentinel_default_reaches_the_tool_as_itself(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    await dispatch(client, "plan.find", {"query": "q"})
    assert feature.calls[0]["cursor"] is _UNSET


@both_dispatches
async def test_a_hidden_parameter_keeps_todays_behaviour(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    await dispatch(client, "plan.dim", {"level": 1})
    await dispatch(client, "plan.dim", {"level": 2, "hidden": "7"})
    assert feature.calls == [{"level": 1, "hidden": 3}, {"level": 2, "hidden": 7}]


@both_dispatches
async def test_parameter_names_that_collide_with_pydantic_dispatch(dispatch: Dispatch) -> None:
    client, feature = _client(_PlanFeature)
    await dispatch(client, "plan.odd", {"model_config": "1", "_private": "p"})
    assert feature.calls == [{"model_config": 1, "_private": "p"}]


# ── hand-built and adjusted tools ──


@both_dispatches
async def test_a_hand_built_tool_receives_its_raw_params(dispatch: Dispatch) -> None:
    client, feature = _client(_HandBuiltFeature)
    params = {"to": "not-an-int", "extra": {"nested": True}}
    assert await dispatch(client, "hand.go", params) == "went"
    assert feature.calls == [params]


@both_dispatches
async def test_a_tool_adjusted_with_replace_is_still_validated(dispatch: Dispatch) -> None:
    client, feature = _client(_EnrichedFeature)
    with pytest.raises(ToolArgumentError, match=r"'enriched\.go'.*x:"):
        await dispatch(client, "enriched.go", {"x": "lots"})
    assert await dispatch(client, "enriched.go", {"x": "4"}) == 4
    assert feature.calls == [{"x": 4}]


# ── the arguments model stays off the wire ──


def test_the_arguments_model_is_kept_beside_the_tool() -> None:
    meta = {t.name: t for t in _PlanFeature().get_tools()}["plan.schedule"]
    assert meta.arguments_model is not None
    hand = _HandBuiltFeature().get_tools()[0]
    assert hand.arguments_model is None


def test_the_arguments_model_is_not_part_of_equality_or_repr() -> None:
    # Each discovery builds a new model class; the tools must still compare equal.
    assert _PlanFeature().get_tools() == _PlanFeature().get_tools()
    assert "arguments_model" not in repr(_PlanFeature().get_tools()[0])


def test_the_arguments_model_never_reaches_the_manifest() -> None:
    client, _ = _client(_PlanFeature)
    wire = json.loads(json.dumps(client.get_registration_manifest()))
    for tool_json in wire["features"][0]["tools"]:
        assert set(tool_json) == {
            "name",
            "description",
            "parameters",
            "input_schema",
            "audience",
            "risk",
        }


def test_unknown_tool_still_raises_key_error() -> None:
    client, _ = _client(_PlanFeature)
    with pytest.raises(KeyError):
        client.dispatch_sync("plan.nope", {})
