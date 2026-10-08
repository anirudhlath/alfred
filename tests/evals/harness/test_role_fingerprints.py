"""The proxy tells roles apart by the first message's text (``ROLE_FINGERPRINTS``).

Readiness and every role-filtered check depend on it, so each fingerprint is pinned to the
first message its role really sends: System 2 and System 1 by driving their send paths with
the model call stubbed out, and the Librarian by reading the first message of every
``litellm.acompletion`` call in its source (see the note on that test).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired, UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.session import SessionManager
from core.reflex.engine import ReflexEngine
from evals.harness.proxy import ROLE_FINGERPRINTS, classify_role

REPO = Path(__file__).resolve().parents[3]
CONSOLIDATOR = REPO / "core" / "librarian" / "consolidator.py"


def _fingerprints(role: str) -> list[str]:
    return [fp for r, fp in ROLE_FINGERPRINTS if r == role]


def test_every_role_with_a_fingerprint_is_pinned_below() -> None:
    assert {role for role, _ in ROLE_FINGERPRINTS} == {"system1", "system2", "librarian"}


def _conscious_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})  # a new session
    redis.hget = AsyncMock(return_value=None)
    redis.get = AsyncMock(return_value=None)  # a new cost day; no stored timezone
    return redis


def _completion(text: str) -> MagicMock:
    message = MagicMock(content=text, tool_calls=None)
    usage = MagicMock(prompt_tokens=10, completion_tokens=2)
    return MagicMock(choices=[MagicMock(message=message)], usage=usage)


@pytest.mark.parametrize(
    ("claim", "channel", "content_type"),
    [
        ("sir", "web_pwa", "text"),  # the shape of the stack's readiness request
        ("guest", "web_pwa", "text"),
        ("sir", "satellite", "audio"),  # adds the voice-delivery prompt
    ],
    ids=["readiness", "guest", "voice"],
)
async def test_system2s_first_message_carries_its_fingerprint(
    claim: str, channel: Any, content_type: Any
) -> None:
    redis = _conscious_redis()
    engine = ConsciousEngine(
        redis=redis,
        identity_gate=IdentityGate(registered_phone="+15550100"),
        session_mgr=SessionManager(redis=redis, timeout_minutes=30),
        cost_tracker=CostTracker(redis=redis, daily_cap_usd=5.0),
        context_assembler=ContextAssembler(),
        domain_router=AsyncMock(),
        tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
        context_reader=AsyncMock(get_rendered_context=AsyncMock(return_value="")),
        claude_model="eval-model",
        claude_api_key="not-a-key",
    )
    request = UserRequest(
        source="alfred-evals",
        channel=channel,
        session_id="eval-ready-1",
        identity_claim=claim,
        content_type=content_type,
        content="Reply with the single word: ready.",
    )
    with patch("litellm.acompletion", return_value=_completion("Ready.")) as model:
        await engine.process_request(request)

    messages = model.call_args.kwargs["messages"]
    assert classify_role(messages) == "system2"
    for fingerprint in _fingerprints("system2"):
        assert fingerprint in messages[0]["content"]


class _CapturingClient:
    """Stands in for the reflex client's httpx client; keeps every body it posts."""

    is_closed = False

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        self.bodies.append(json)
        reply = {"choices": [{"message": {"content": '{"action": "none"}'}}], "usage": {}}
        return httpx.Response(200, json=reply, request=httpx.Request("POST", url))


@pytest.mark.parametrize("path", ["state_change", "trigger_fired"])
async def test_system1s_first_message_carries_its_fingerprint(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from core.reflex import openai_client

    # The container's settings (container_env): System 1 on the OpenAI-compatible backend.
    monkeypatch.setenv("REFLEX_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_COMPAT_HOST", "http://proxy.test")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "eval-model")
    client = _CapturingClient()
    monkeypatch.setattr(openai_client, "_get_client", lambda: client)
    engine = ReflexEngine(
        preferences_dir="/unused",
        tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
        memory_reader=MagicMock(get_preferences=MagicMock(return_value="")),
    )
    if path == "state_change":
        event = StateChangedEvent(
            source="home-service",
            domain="home",
            entity_id="light.kitchen",
            old_state="off",
            new_state="on",
            attributes={},
        )
        await engine.process_event(event)
    else:
        fired = TriggerFired(trigger_id="t-1", trigger_name="evening", trigger_type="time")
        await engine.process_trigger_fired(fired)

    (body,) = client.bodies
    assert classify_role(body["messages"]) == "system1"
    for fingerprint in _fingerprints("system1"):
        assert fingerprint in body["messages"][0]["content"]


def _leading_text(node: ast.expr) -> str:
    """The literal text an expression starts with: its whole value for a string, the text
    before the first placeholder for an f-string, the left side's for ``a + b``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        text = ""
        for part in node.values:
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                break
            text += part.value
        return text
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _leading_text(node.left)
    return ""


def _resolve(name: str, before: int, function: ast.AST) -> ast.expr:
    """The value last assigned to *name* in *function* before line *before*."""
    assigned = [
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Assign)
        and n.lineno < before
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    ]
    assert assigned, f"{name} is never assigned before line {before}"
    return max(assigned, key=lambda n: n.lineno).value


def _first_messages(source: Path) -> dict[int, str]:
    """{line: leading text of the first message} for each ``litellm.acompletion`` call."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found: dict[int, str] = {}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "acompletion"
            ):
                continue
            (messages,) = [k.value for k in call.keywords if k.arg == "messages"]
            assert isinstance(messages, ast.List) and messages.elts, call.lineno
            first = messages.elts[0]
            assert isinstance(first, ast.Dict), call.lineno
            (content,) = [
                v
                for k, v in zip(first.keys, first.values, strict=True)
                if isinstance(k, ast.Constant) and k.value == "content"
            ]
            if isinstance(content, ast.Name):
                content = _resolve(content.id, call.lineno, function)
            found[call.lineno] = _leading_text(content)
    return found


def test_every_librarian_llm_call_opens_with_a_librarian_fingerprint() -> None:
    # Read from the source, not driven: each of these five calls sits behind its own
    # Librarian state (episodic and cold stores, a routine store, the user's timezone in
    # Redis, observation batches, conflicting preferences, compression groups), so driving
    # them means five consolidator fixtures. The readiness gate keys on System 2 alone.
    firsts = _first_messages(CONSOLIDATOR)
    assert len(firsts) >= len(_fingerprints("librarian")), firsts
    for line, text in firsts.items():
        role = classify_role([{"role": "system", "content": text}])
        assert role == "librarian", f"consolidator.py:{line} opens with {text[:60]!r}"
    for fingerprint in _fingerprints("librarian"):
        assert any(fingerprint in text for text in firsts.values()), fingerprint
