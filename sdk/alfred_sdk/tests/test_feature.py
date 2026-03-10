"""Tests for BaseFeature and @tool decorator."""

from __future__ import annotations

from typing import Any

from alfred_sdk.feature import BaseFeature, _parse_google_docstring_args, tool


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
    def dim_lights(self, room: str, level: int) -> dict:
        """Dim the lights in a room.

        Args:
            room: The room to dim.
            level: Brightness level 0-100.
        """
        self.ha_called = True
        return {"room": room, "level": level}

    @tool(description="Custom turn off description")
    def turn_off(self, room: str) -> dict:
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
        def do_thing(self, data: dict[str, Any], items: list[str]) -> dict:
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
        def do_thing(self, x: int, y: int = 42) -> dict:
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
        def do_thing(self, x: int) -> dict:
            """Do it."""
            return {}

    feature = _OverrideFeature()
    tools = feature.get_tools()
    assert tools[0].name == "custom.my_tool"
