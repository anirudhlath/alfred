"""Tests for MCP tool serving."""

import pytest


def test_mcp_tool_decorator_preserves_function():
    from sdk.alfred_sdk.mcp import mcp_tool

    @mcp_tool(name="test.add", description="Add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    assert hasattr(add, "_mcp_tool_meta")
    assert add._mcp_tool_meta["name"] == "test.add"


def test_mcp_tool_extracts_parameter_schema():
    from sdk.alfred_sdk.mcp import mcp_tool

    @mcp_tool(name="test.greet", description="Greet someone")
    def greet(name: str, excited: bool = False) -> str:
        return f"Hello {name}{'!' if excited else '.'}"

    meta = greet._mcp_tool_meta
    assert "name" in meta["parameters"]
    assert "excited" in meta["parameters"]
