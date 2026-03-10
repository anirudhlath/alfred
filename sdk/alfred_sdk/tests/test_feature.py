"""Tests for BaseFeature and @tool decorator."""

from __future__ import annotations

from alfred_sdk.feature import BaseFeature, ToolMeta, tool


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
