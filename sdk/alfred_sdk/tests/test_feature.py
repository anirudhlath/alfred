"""Tests for BaseFeature and @tool decorator."""

from __future__ import annotations

from alfred_sdk.feature import ToolMeta, tool


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
