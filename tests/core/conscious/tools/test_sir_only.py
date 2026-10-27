"""Personal toolsets reach the verified user only, checked when offered and when called."""

from __future__ import annotations

import pytest
from pydantic_ai import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from core.conscious.tools.scope import allowed, refusal
from core.conscious.tools.sir_only import SirOnlyToolset
from tests.core.conscious.helpers import call_direct, make_deps, run_context


def _personal() -> SirOnlyToolset:
    def diary() -> str:
        """Read the diary."""
        return "secret"

    return SirOnlyToolset(FunctionToolset([diary]))


@pytest.mark.parametrize(
    ("risk", "who", "ok"),
    [
        ("benign", "guest", True),
        ("elevated", "guest", False),
        ("critical", "guest", False),
        ("personal", "guest", False),
        ("critical", "sir", True),
        ("personal", "sir", True),
    ],
)
def test_allowed(risk: str, who: str, ok: bool) -> None:
    assert allowed(risk, who) is ok


def test_refusal_is_neutral() -> None:
    assert refusal("diary") == "diary is only available to the verified user"


@pytest.mark.asyncio
async def test_sir_is_offered_personal_tools_and_a_guest_is_not() -> None:
    toolset = _personal()
    assert list(await toolset.get_tools(run_context(make_deps("sir")))) == ["diary"]
    assert await toolset.get_tools(run_context(make_deps("guest"))) == {}


@pytest.mark.asyncio
async def test_sir_only_refuses_a_guest_at_call_time() -> None:
    with pytest.raises(ToolFailed, match="diary is only available to the verified user"):
        await call_direct(
            _personal(), "diary", {}, offered_to=make_deps("sir"), called_by=make_deps("guest")
        )
