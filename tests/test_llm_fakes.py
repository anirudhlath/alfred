"""The scripted model every agent test drives."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, ToolFailed

from tests.llm_fakes import Script, ScriptExhausted, tool_call, tool_calls


class _Item(BaseModel):
    a: int


@pytest.mark.asyncio
async def test_script_answers_in_order_and_records_each_request() -> None:
    script = Script(tool_call("t", {"x": 1}), "done")
    agent = Agent(script.model, instructions="be brief")

    @agent.tool_plain
    def t(x: int) -> dict[str, int]:
        return {"ok": x}

    result = await agent.run("go")

    assert result.output == "done"
    assert script.call_count == 2
    assert script.offered(0) == ["t"]
    assert script.instructions(0) == "be brief"
    assert script.returns(1) == ['{"ok":1}']
    assert script.retries(1) == []


@pytest.mark.asyncio
async def test_outcomes_tell_a_failed_result_from_a_successful_one() -> None:
    script = Script(tool_calls(("ok", {}), ("broken", {})), "done")
    agent = Agent(script.model)

    @agent.tool_plain
    def ok() -> str:
        return "fine"

    @agent.tool_plain
    def broken() -> str:
        raise ToolFailed("offline")

    await agent.run("go")

    assert script.returns(1) == ["fine", "offline"]
    assert script.outcomes(1) == ["success", "failed"]


@pytest.mark.asyncio
async def test_running_out_of_replies_fails_the_test() -> None:
    with pytest.raises(ScriptExhausted):
        await Agent(Script().model).run("go")


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_structured_replies_follow_the_model_profile(native: bool) -> None:
    script = Script([{"a": 1}], json_schema_output=native)
    output_type = NativeOutput(list[_Item]) if native else list[_Item]

    result = await Agent(script.model, output_type=output_type).run("go")

    assert result.output == [_Item(a=1)]
