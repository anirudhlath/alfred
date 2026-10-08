"""Tests for BaseFeature and @tool decorator."""

from __future__ import annotations

import dataclasses
import datetime as _dt  # noqa: TC003 — get_type_hints and the pydantic model need it at runtime
import enum
import json
import re
import warnings
from collections.abc import Callable  # noqa: TC003 — get_type_hints resolves it at runtime
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from sdk.alfred_sdk.feature import (
    BaseFeature,
    ServiceManifest,
    ToolMeta,
    ToolParameter,
    _parse_google_docstring_args,
    check_object_schema,
    input_schema_from_parameters,
    tool,
)


def test_tool_decorator_marks_method() -> None:
    """@tool sets _tool_marker on the function."""

    @tool
    def my_func(x: int) -> str:
        """Do something."""
        return str(x)

    assert my_func._tool_marker is True  # type: ignore[attr-defined]


def test_tool_decorator_preserves_function() -> None:
    """@tool doesn't change function behavior."""

    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    assert add(1, 2) == 3


def test_tool_decorator_with_overrides() -> None:
    """@tool(description=...) overrides docstring extraction."""

    @tool(description="Custom description", name="custom.name")
    def my_func(x: int) -> str:
        """Original description."""
        return str(x)

    assert my_func._tool_marker is True  # type: ignore[attr-defined]
    assert my_func._tool_overrides["description"] == "Custom description"  # type: ignore[attr-defined]
    assert my_func._tool_overrides["name"] == "custom.name"  # type: ignore[attr-defined]


# ── BaseFeature tests ──


class _StubFeature(BaseFeature):
    """A test feature for lighting."""

    feature_name = "test_lighting"

    def __init__(self) -> None:
        super().__init__()
        self.ha_called = False

    @tool
    def dim_lights(self, room: str, level: int) -> dict[str, Any]:
        """Dim the lights in a room.

        Args:
            room: The room to dim.
            level: Brightness level 0-100.
        """
        self.ha_called = True
        return {"room": room, "level": level}

    @tool(description="Custom turn off description")
    def turn_off(self, room: str) -> dict[str, Any]:
        """Original description."""
        return {"room": room}

    def helper_method(self) -> None:
        """Not a tool — no @tool decorator."""


def test_base_feature_get_tools_returns_tool_meta() -> None:
    feature = _StubFeature()
    tools = feature.get_tools()
    assert len(tools) == 2

    names = {t.name for t in tools}
    assert "test_lighting.dim_lights" in names
    assert "test_lighting.turn_off" in names


def test_base_feature_get_tools_extracts_params() -> None:
    feature = _StubFeature()
    tools = {t.name: t for t in feature.get_tools()}

    dim = tools["test_lighting.dim_lights"]
    assert "room" in dim.parameters
    assert dim.parameters["room"].type == "str"
    assert dim.parameters["room"].description == "The room to dim."
    assert "level" in dim.parameters
    assert dim.parameters["level"].type == "int"


def test_base_feature_get_tools_uses_overrides() -> None:
    feature = _StubFeature()
    tools = {t.name: t for t in feature.get_tools()}

    turn_off = tools["test_lighting.turn_off"]
    assert turn_off.description == "Custom turn off description"


def test_base_feature_get_tools_skips_non_tool_methods() -> None:
    feature = _StubFeature()
    tools = feature.get_tools()
    names = {t.name for t in tools}
    assert "test_lighting.helper_method" not in names


def test_base_feature_get_tools_no_docstring() -> None:
    class _NoDocFeature(BaseFeature):
        feature_name = "nodoc"

        @tool
        def do_thing(self, x: int) -> int:
            return x

    feature = _NoDocFeature()
    tools = feature.get_tools()
    assert len(tools) == 1
    assert tools[0].description == ""


def test_base_feature_description_from_class_docstring() -> None:
    feature = _StubFeature()
    assert feature.get_description() == "A test feature for lighting."


def test_base_feature_to_manifest() -> None:
    feature = _StubFeature()
    manifest = feature.to_manifest()
    assert manifest.name == "test_lighting"
    assert manifest.description == "A test feature for lighting."
    assert len(manifest.tools) == 2


# ── Docstring parser edge cases ──


def test_parse_google_docstring_basic() -> None:
    doc = """Do something.

    Args:
        room: The room name.
        level: Brightness 0-100.
    """
    args = _parse_google_docstring_args(doc)
    assert args["room"] == "The room name."
    assert args["level"] == "Brightness 0-100."


def test_parse_google_docstring_multiline_desc() -> None:
    doc = """Do something.

    Args:
        room: The room name, which can be
            a multi-line description.
        level: Brightness.
    """
    args = _parse_google_docstring_args(doc)
    assert args["room"] == "The room name, which can be a multi-line description."
    assert args["level"] == "Brightness."


def test_parse_google_docstring_no_args_section() -> None:
    doc = """Do something without args."""
    args = _parse_google_docstring_args(doc)
    assert args == {}


def test_parse_google_docstring_empty() -> None:
    args = _parse_google_docstring_args("")
    assert args == {}


def test_parse_google_docstring_args_then_returns() -> None:
    doc = """Do something.

    Args:
        x: The input.

    Returns:
        The output.
    """
    args = _parse_google_docstring_args(doc)
    assert args == {"x": "The input."}


def test_tool_meta_complex_types() -> None:
    """Complex type hints use str() representation."""

    class _ComplexFeature(BaseFeature):
        feature_name = "complex"

        @tool
        def do_thing(self, data: dict[str, Any], items: list[str]) -> dict[str, Any]:
            """Process data.

            Args:
                data: Input data mapping.
                items: List of items.
            """
            return {}

    feature = _ComplexFeature()
    tools = {t.name: t for t in feature.get_tools()}
    t = tools["complex.do_thing"]
    # Complex types use str() representation
    assert "dict" in t.parameters["data"].type
    assert "list" in t.parameters["items"].type


def test_tool_meta_default_values() -> None:
    """Default parameter values are captured."""

    class _DefaultFeature(BaseFeature):
        feature_name = "defaults"

        @tool
        def do_thing(self, x: int, y: int = 42) -> dict[str, Any]:
            """Process.

            Args:
                x: Required param.
                y: Optional param.
            """
            return {}

    feature = _DefaultFeature()
    tools = {t.name: t for t in feature.get_tools()}
    t = tools["defaults.do_thing"]
    assert t.parameters["x"].default is None  # No default
    assert t.parameters["y"].default == 42


def test_tool_name_override_in_get_tools() -> None:
    """@tool(name=...) overrides the qualified name in get_tools()."""

    class _OverrideFeature(BaseFeature):
        feature_name = "over"

        @tool(name="custom.my_tool")
        def do_thing(self, x: int) -> dict[str, Any]:
            """Do it."""
            return {}

    feature = _OverrideFeature()
    tools = feature.get_tools()
    assert tools[0].name == "custom.my_tool"


# ── Credential models (contract C1) ──


def test_credential_field_defaults() -> None:
    from sdk.alfred_sdk.feature import CredentialField

    field = CredentialField(label="HA URL")
    assert field.field_type == "text"
    assert field.required is True
    assert field.placeholder == ""
    assert field.default == ""
    assert field.help_text == ""
    assert field.transient is False


def test_credential_schema_dumps_fields() -> None:
    from sdk.alfred_sdk.feature import CredentialField, CredentialSchema

    schema = CredentialSchema(fields={"url": CredentialField(label="HA URL", field_type="url")})
    dumped = schema.model_dump()
    assert dumped["fields"]["url"]["label"] == "HA URL"
    assert dumped["fields"]["url"]["field_type"] == "url"


def test_service_manifest_credential_fields_default_none() -> None:
    from sdk.alfred_sdk.feature import ServiceManifest

    manifest = ServiceManifest(service_name="svc", service_endpoint="http://x/mcp")
    dumped = manifest.model_dump()
    assert dumped["credentials_schema"] is None
    assert dumped["credentials_endpoint"] is None


def test_service_manifest_carries_credentials() -> None:
    from sdk.alfred_sdk.feature import CredentialField, CredentialSchema, ServiceManifest

    manifest = ServiceManifest(
        service_name="home-service",
        service_endpoint="http://localhost:8000/mcp",
        credentials_schema=CredentialSchema(
            fields={"token": CredentialField(label="Token", field_type="password")}
        ),
        credentials_endpoint="http://localhost:8000/credentials",
    )
    dumped = manifest.model_dump()
    assert dumped["credentials_endpoint"] == "http://localhost:8000/credentials"
    assert dumped["credentials_schema"]["fields"]["token"]["field_type"] == "password"


# ── audience / risk (contract C1) ──


def test_tool_decorator_default_audience_and_risk() -> None:
    @tool
    def my_tool(x: int) -> str:
        """Do something."""
        return str(x)

    assert my_tool._tool_overrides["audience"] == "conscious"  # type: ignore[attr-defined]
    assert my_tool._tool_overrides["risk"] == "benign"  # type: ignore[attr-defined]


def test_tool_decorator_audience_and_risk_kwargs() -> None:
    @tool(audience="reflex", risk="critical")
    def my_tool(x: int) -> str:
        """Do something."""
        return str(x)

    assert my_tool._tool_overrides["audience"] == "reflex"  # type: ignore[attr-defined]
    assert my_tool._tool_overrides["risk"] == "critical"  # type: ignore[attr-defined]


class _TaggedFeature(BaseFeature):
    """Feature with audience/risk-tagged tools."""

    feature_name = "tagged"

    @tool(audience="reflex")
    def turn_on(self, room: str) -> dict[str, Any]:
        """Turn on lights.

        Args:
            room: The room.
        """
        return {"room": room}

    @tool(risk="critical")
    def unlock(self, door: str) -> dict[str, Any]:
        """Unlock a door.

        Args:
            door: The door.
        """
        return {"door": door}


def test_get_tools_carries_audience_and_risk() -> None:
    feature = _TaggedFeature()
    metas = {t.name: t for t in feature.get_tools()}
    assert metas["tagged.turn_on"].audience == "reflex"
    assert metas["tagged.turn_on"].risk == "benign"
    assert metas["tagged.unlock"].audience == "conscious"
    assert metas["tagged.unlock"].risk == "critical"


def test_to_manifest_carries_audience_and_risk() -> None:
    feature = _TaggedFeature()
    manifest_tools = {t.name: t for t in feature.to_manifest().tools}
    assert manifest_tools["tagged.turn_on"].audience == "reflex"
    assert manifest_tools["tagged.unlock"].risk == "critical"

    dumped = feature.to_manifest().model_dump()
    by_name = {t["name"]: t for t in dumped["tools"]}
    assert by_name["tagged.turn_on"]["audience"] == "reflex"
    assert by_name["tagged.turn_on"]["risk"] == "benign"
    assert by_name["tagged.unlock"]["audience"] == "conscious"
    assert by_name["tagged.unlock"]["risk"] == "critical"


# ── input_schema: assembled from ToolParameters (hand-built tools) ──


def test_hand_built_tool_meta_derives_input_schema() -> None:
    meta = ToolMeta(
        name="home.light_turn_on",
        description="Turn on a light.",
        parameters={
            "target": ToolParameter(type="str", description="Area or entity.", required=True),
            "brightness": ToolParameter(type="float", description="0-255."),
            "data": ToolParameter(type="dict"),
        },
    )
    assert meta.input_schema == {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Area or entity."},
            "brightness": {"type": "number", "description": "0-255."},
            "data": {"type": "object"},
        },
        "required": ["target"],
    }


def test_parameter_json_schema_overrides_the_type_name() -> None:
    schema = input_schema_from_parameters(
        {
            "mode": ToolParameter(
                type="str",
                description="Fan mode.",
                json_schema={"type": "string", "enum": ["low", "high"]},
            )
        }
    )
    assert schema["properties"]["mode"] == {
        "type": "string",
        "enum": ["low", "high"],
        "description": "Fan mode.",
    }


def test_legacy_type_names_map_like_before() -> None:
    schema = input_schema_from_parameters(
        {
            "when": ToolParameter(type="datetime.datetime | None"),
            "items": ToolParameter(type="list[str]"),
            "n": ToolParameter(type="int"),
            "flag": ToolParameter(type="bool"),
            "odd": ToolParameter(type="SomethingElse"),
        }
    )
    assert {name: prop["type"] for name, prop in schema["properties"].items()} == {
        "when": "string",
        "items": "array",
        "n": "integer",
        "flag": "boolean",
        "odd": "string",
    }


def test_zero_parameter_tool_has_an_empty_object_schema() -> None:
    meta = ToolMeta(name="x.ping", description="Ping.", parameters={})
    assert meta.input_schema == {"type": "object", "properties": {}, "required": []}


def test_explicit_input_schema_is_kept() -> None:
    explicit = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    meta = ToolMeta(name="x.q", description="Q.", parameters={}, input_schema=explicit)
    assert meta.input_schema == explicit


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param("oops", id="not-a-dict"),
        pytest.param({"properties": {}}, id="no-type"),
        pytest.param({"type": "array"}, id="not-object-type"),
        pytest.param({"type": "object", "properties": ["p"]}, id="non-dict-properties"),
        pytest.param({"type": "object", "properties": {"p": "x"}}, id="non-dict-property"),
        pytest.param({"type": "object", "required": "p"}, id="non-list-required"),
        pytest.param({"type": "object", "required": [1]}, id="non-string-required"),
    ],
)
def test_check_object_schema_rejects_what_is_not_an_object_schema(schema: Any) -> None:
    with pytest.raises(ValueError, match="input_schema"):
        check_object_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {"type": "object", "properties": {"p": {}}, "required": ["p"]},
    ],
)
def test_check_object_schema_hands_back_an_object_schema(schema: dict[str, Any]) -> None:
    assert check_object_schema(schema) is schema


def test_explicit_input_schema_that_is_not_an_object_schema_fails() -> None:
    with pytest.raises(TypeError, match=r"^Tool 'x\.q': input_schema"):
        ToolMeta(name="x.q", description="Q.", parameters={}, input_schema={"type": "array"})


def test_mutating_the_callers_input_schema_leaves_the_meta_alone() -> None:
    explicit: dict[str, Any] = {
        "type": "object",
        "properties": {"q": {"type": "string", "enum": ["a"]}},
        "required": ["q"],
    }
    meta = ToolMeta(name="x.q", description="Q.", parameters={}, input_schema=explicit)
    explicit["properties"]["q"]["enum"].append("b")
    explicit["required"].clear()
    assert meta.input_schema == {
        "type": "object",
        "properties": {"q": {"type": "string", "enum": ["a"]}},
        "required": ["q"],
    }


def test_mutating_the_callers_json_schema_leaves_the_parameter_alone() -> None:
    json_schema: dict[str, Any] = {"type": "string", "enum": ["low", "high"]}
    param = ToolParameter(type="str", json_schema=json_schema)
    meta = ToolMeta(name="x.m", description="M.", parameters={"mode": param})
    json_schema["enum"].append("max")
    json_schema["type"] = "integer"
    assert param.json_schema == {"type": "string", "enum": ["low", "high"]}
    assert meta.input_schema["properties"]["mode"] == {"type": "string", "enum": ["low", "high"]}


def test_replace_keeps_input_schema_audience_and_risk() -> None:
    meta = ToolMeta(
        name="x.q",
        description="Q.",
        parameters={"q": ToolParameter(type="str", required=True)},
        audience="reflex",
        risk="critical",
    )
    copy = dataclasses.replace(meta, description="Q, enriched.")
    assert copy.input_schema == meta.input_schema
    assert (copy.audience, copy.risk) == ("reflex", "critical")


def test_replace_re_derives_the_schema_only_when_told_to() -> None:
    meta = ToolMeta(
        name="x.q", description="Q.", parameters={"q": ToolParameter(type="str", required=True)}
    )
    new = {"n": ToolParameter(type="int", required=True)}
    assert dataclasses.replace(meta, parameters=new).input_schema == meta.input_schema
    assert dataclasses.replace(meta, parameters=new, input_schema={}).input_schema == {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }


class _HandBuiltFeature(BaseFeature):
    """Tools built without a signature, like home-service's."""

    feature_name = "hand"

    def get_tools(self) -> list[ToolMeta]:
        return [
            ToolMeta(
                name="hand.go",
                description="Go somewhere.",
                parameters={"to": ToolParameter(type="str", description="Where.", required=True)},
            )
        ]


def test_manifest_carries_input_schema_through_json() -> None:
    manifest = ServiceManifest(
        service_name="svc",
        service_endpoint="http://svc/mcp",
        features=[_HandBuiltFeature().to_manifest()],
    )
    wire = json.loads(json.dumps(manifest.model_dump()))
    tool_json = wire["features"][0]["tools"][0]
    assert tool_json["input_schema"] == {
        "type": "object",
        "properties": {"to": {"type": "string", "description": "Where."}},
        "required": ["to"],
    }
    # The legacy map stays on the wire, now saying which parameters are required.
    assert tool_json["parameters"]["to"]["required"] is True


# ── input_schema: generated from @tool signatures ──


class _Speed(enum.Enum):
    SLOW = "slow"
    FAST = "fast"


class _Window(BaseModel):
    start: _dt.datetime
    title: str = ""


class _ShapesFeature(BaseFeature):
    feature_name = "shapes"

    @tool
    def plan(
        self,
        title: str,
        tags: list[str],
        mode: Literal["a", "b"],
        speed: _Speed,
        at: _dt.datetime,
        window: _Window | None = None,
        limit: int = 5,
        default: str = "x",
        meta: dict[str, Any] | None = None,
        style: dict[str, str] = {"title": "bold"},  # noqa: B006 — data containing "title"
    ) -> dict[str, Any]:
        """Plan something.

        Args:
            title: What to call it.
            tags: Labels to attach.
        """
        return {}


def _schema(feature: BaseFeature, name: str) -> dict[str, Any]:
    return {t.name: t for t in feature.get_tools()}[name].input_schema


def test_signature_schema_carries_rich_types() -> None:
    schema = _schema(_ShapesFeature(), "shapes.plan")
    props = schema["properties"]
    assert schema["type"] == "object"
    assert props["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Labels to attach.",
    }
    assert props["mode"] == {"type": "string", "enum": ["a", "b"]}
    assert props["speed"] == {"$ref": "#/$defs/_Speed"}
    assert schema["$defs"]["_Speed"] == {"type": "string", "enum": ["slow", "fast"]}
    assert props["at"] == {"type": "string", "format": "date-time"}
    assert props["window"] == {
        "anyOf": [{"$ref": "#/$defs/_Window"}, {"type": "null"}],
        "default": None,
    }
    assert schema["$defs"]["_Window"]["required"] == ["start"]
    assert props["limit"] == {"type": "integer", "default": 5}
    assert props["meta"]["anyOf"][0] == {"type": "object", "additionalProperties": True}


def test_required_is_exactly_the_parameters_without_defaults() -> None:
    meta = {t.name: t for t in _ShapesFeature().get_tools()}["shapes.plan"]
    assert meta.input_schema["required"] == ["title", "tags", "mode", "speed", "at"]
    assert [n for n, p in meta.parameters.items() if p.required] == [
        "title",
        "tags",
        "mode",
        "speed",
        "at",
    ]


def test_title_stripping_keeps_names_and_data() -> None:
    schema = _schema(_ShapesFeature(), "shapes.plan")
    assert "title" not in schema
    assert "title" not in schema["$defs"]["_Speed"]
    assert "title" not in schema["$defs"]["_Window"]
    # A parameter named `title`, a model field named `title`, a parameter named
    # `default`, and data containing a "title" key all survive.
    assert schema["properties"]["title"] == {"type": "string", "description": "What to call it."}
    assert schema["$defs"]["_Window"]["properties"]["title"] == {"type": "string", "default": ""}
    assert schema["properties"]["default"] == {"type": "string", "default": "x"}
    assert schema["properties"]["style"]["default"] == {"title": "bold"}


class _TitlePart(BaseModel):
    kind: Literal["title"]


class _BodyPart(BaseModel):
    kind: Literal["body"]


class _PartsFeature(BaseFeature):
    feature_name = "parts"

    @tool
    def add(
        self, part: Annotated[_TitlePart | _BodyPart, Field(discriminator="kind")]
    ) -> dict[str, Any]:
        """Add a part."""
        return {}


def test_title_stripping_keeps_a_discriminator_tag_named_title() -> None:
    discriminator = _schema(_PartsFeature(), "parts.add")["properties"]["part"]["discriminator"]
    assert discriminator == {
        "propertyName": "kind",
        "mapping": {"title": "#/$defs/_TitlePart", "body": "#/$defs/_BodyPart"},
    }


class _AnnotatedFeature(BaseFeature):
    feature_name = "annotated"

    @tool
    def dim(
        self,
        level: Annotated[int, Field(ge=0, le=100, description="Brightness")],
        hidden: Annotated[int, SkipJsonSchema()] = 3,
    ) -> dict[str, Any]:
        """Dim."""
        return {}


def test_annotated_constraints_reach_the_schema() -> None:
    meta = {t.name: t for t in _AnnotatedFeature().get_tools()}["annotated.dim"]
    assert meta.input_schema["properties"]["level"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 100,
        "description": "Brightness",
    }
    # The legacy type name is the bare type's, as before Annotated was kept.
    assert meta.parameters["level"].type == "int"
    # A parameter the schema skips has no JSON default to carry.
    assert "hidden" not in meta.input_schema["properties"]
    assert meta.parameters["hidden"].default is None
    assert meta.parameters["hidden"].type == "int"


class _DescribedTwiceFeature(BaseFeature):
    feature_name = "described"

    @tool
    def dim(self, level: Annotated[int, Field(ge=0, description="From Field.")]) -> dict[str, Any]:
        """Dim.

        Args:
            level: From the docstring.
        """
        return {}


def test_docstring_description_wins_over_field_description() -> None:
    assert _schema(_DescribedTwiceFeature(), "described.dim")["properties"]["level"] == {
        "type": "integer",
        "minimum": 0,
        "description": "From the docstring.",
    }


_UNSET = object()


class _SentinelFeature(BaseFeature):
    feature_name = "sentinel"

    @tool
    def find(self, query: str, cursor: Any = _UNSET) -> dict[str, Any]:
        """Find things."""
        return {}


def test_unserializable_default_stays_optional_without_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = _schema(_SentinelFeature(), "sentinel.find")
    assert schema["required"] == ["query"]
    assert "default" not in schema["properties"]["cursor"]


def test_sentinel_default_still_registers() -> None:
    manifest = ServiceManifest(
        service_name="svc",
        service_endpoint="http://svc/mcp",
        features=[_SentinelFeature().to_manifest()],
    )
    dumped = json.loads(json.dumps(manifest.model_dump()))
    assert dumped["features"][0]["tools"][0]["parameters"]["cursor"]["default"] is None


class _EnumDefaultFeature(BaseFeature):
    feature_name = "enumdefault"

    @tool
    def go(self, speed: _Speed = _Speed.SLOW) -> dict[str, Any]:
        """Go."""
        return {}


def test_enum_default_is_carried_as_its_json_value() -> None:
    meta = {t.name: t for t in _EnumDefaultFeature().get_tools()}["enumdefault.go"]
    assert meta.parameters["speed"].default == "slow"
    assert meta.input_schema["properties"]["speed"]["default"] == "slow"
    dumped = json.loads(json.dumps(_EnumDefaultFeature().to_manifest().model_dump()))
    assert dumped["tools"][0]["parameters"]["speed"]["default"] == "slow"


class _OddNamesFeature(BaseFeature):
    feature_name = "odd"

    @tool
    def odd(self, model_config: int, _private: str = "", schema: str = "") -> dict[str, Any]:
        """Parameter names that collide with BaseModel internals."""
        return {}


def test_parameter_names_never_collide_with_pydantic() -> None:
    schema = _schema(_OddNamesFeature(), "odd.odd")
    assert set(schema["properties"]) == {"model_config", "_private", "schema"}
    assert schema["required"] == ["model_config"]


class _NoParamsFeature(BaseFeature):
    feature_name = "none"

    @tool
    def ping(self) -> dict[str, Any]:
        """Ping."""
        return {}


def test_no_parameter_tool_schema() -> None:
    assert _schema(_NoParamsFeature(), "none.ping") == {
        "type": "object",
        "properties": {},
        "required": [],
    }


class _VarArgsFeature(BaseFeature):
    feature_name = "varargs"

    @tool
    def bad(self, *names: str) -> dict[str, Any]:
        """Varargs."""
        return {}


class _KwArgsFeature(BaseFeature):
    feature_name = "kwargs"

    @tool
    def bad(self, **options: str) -> dict[str, Any]:
        """Kwargs."""
        return {}


class _PositionalOnlyFeature(BaseFeature):
    feature_name = "posonly"

    @tool
    def bad(self, x: int, /) -> dict[str, Any]:
        """Positional-only."""
        return {}


class _UnresolvableFeature(BaseFeature):
    feature_name = "unresolvable"

    @tool
    def bad(self, thing: NotDefinedAnywhere) -> dict[str, Any]:  # type: ignore[name-defined]  # noqa: F821
        """A hint that cannot be resolved."""
        return {}


class _Opaque:
    """A type Pydantic cannot describe."""


class _OpaqueFeature(BaseFeature):
    feature_name = "opaque"

    @tool
    def bad(self, thing: _Opaque) -> dict[str, Any]:
        """A type Pydantic cannot describe."""
        return {}


class _SkippedChoiceFeature(BaseFeature):
    feature_name = "skipped"

    @tool
    def bad(self, count: int | Callable[[], int]) -> dict[str, Any]:
        """A union Pydantic would quietly narrow to `int`."""
        return {}


class _Dangling(BaseModel):
    thing: NotDefinedEither  # type: ignore[name-defined]  # noqa: F821


class _DanglingModelFeature(BaseFeature):
    feature_name = "dangling"

    @tool
    def bad(self, model: _Dangling) -> dict[str, Any]:
        """A model whose own forward reference never resolves."""
        return {}


class _HiddenRequiredFeature(BaseFeature):
    feature_name = "hidden"

    @tool
    def bad(self, secret: Annotated[int, SkipJsonSchema()]) -> dict[str, Any]:
        """A required parameter no model could ever be shown, and so never supply."""
        return {}


class _FieldDefaultFeature(BaseFeature):
    feature_name = "fielddefault"

    @tool
    def bad(self, x: int = Field(5, ge=0)) -> dict[str, Any]:
        """A default the call never applies: Python passes the FieldInfo itself."""
        return {}


class _AnnotatedDefaultFeature(BaseFeature):
    feature_name = "annotateddefault"

    @tool
    def bad(self, n: Annotated[int, Field(default=5)]) -> dict[str, Any]:
        """A default only the schema knows: a call without `n` fails."""
        return {}


class _AnnotatedFactoryFeature(BaseFeature):
    feature_name = "annotatedfactory"

    @tool
    def bad(self, tags: Annotated[list[str], Field(default_factory=list)]) -> dict[str, Any]:
        """A default factory only the schema knows: a call without `tags` fails."""
        return {}


_FIELD_DEFAULT_FIX = r"put the default on the parameter itself"


@pytest.mark.parametrize(
    ("feature", "name", "message"),
    [
        (
            _HiddenRequiredFeature,
            "hidden.bad",
            r"parameter 'secret' has no default but is hidden from its JSON Schema",
        ),
        (_FieldDefaultFeature, "fielddefault.bad", rf"parameter 'x' .*{_FIELD_DEFAULT_FIX}"),
        (
            _AnnotatedDefaultFeature,
            "annotateddefault.bad",
            rf"parameter 'n' .*{_FIELD_DEFAULT_FIX}",
        ),
        (
            _AnnotatedFactoryFeature,
            "annotatedfactory.bad",
            rf"parameter 'tags' .*{_FIELD_DEFAULT_FIX}",
        ),
        (_VarArgsFeature, "varargs.bad", r"parameter 'names' must be passable by keyword"),
        (_KwArgsFeature, "kwargs.bad", r"parameter 'options' must be passable by keyword"),
        (_PositionalOnlyFeature, "posonly.bad", r"parameter 'x' must be passable by keyword"),
        (_UnresolvableFeature, "unresolvable.bad", r"cannot resolve its type hints"),
        (_OpaqueFeature, "opaque.bad", r"cannot describe its parameters as JSON Schema"),
        (_SkippedChoiceFeature, "skipped.bad", r"cannot describe .*skipped-choice"),
        (_DanglingModelFeature, "dangling.bad", r"cannot describe its parameters as JSON Schema"),
    ],
)
def test_undescribable_signatures_fail_at_discovery(
    feature: type[BaseFeature], name: str, message: str
) -> None:
    with pytest.raises(TypeError, match=rf"^Tool '{re.escape(name)}': .*{message}"):
        feature().get_tools()


class _ConstrainedDefaultFeature(BaseFeature):
    feature_name = "constrained"

    @tool
    def go(self, x: Annotated[int, Field(ge=0)] = 5) -> dict[str, Any]:
        """A constraint in Annotated, the default on the parameter: the supported form."""
        return {}


def test_a_default_beside_annotated_constraints_reaches_the_schema() -> None:
    meta = {t.name: t for t in _ConstrainedDefaultFeature().get_tools()}["constrained.go"]
    assert meta.input_schema["properties"]["x"] == {"type": "integer", "minimum": 0, "default": 5}
    assert meta.input_schema["required"] == []
    assert meta.parameters["x"].required is False
    assert meta.parameters["x"].default == 5
