"""An OpenAI-compatible pass-through to vLLM that records every chat completion.

System 2 sends its tool calls to the model and to home-service, never onto a stream, so
this is the only place their arguments can be seen. Roles are told apart by the first
message's text (``ROLE_FINGERPRINTS``); a test pins those strings to the prompt sources.

The recorder never changes a reply: a chat request goes upstream as the bytes Alfred sent,
and the reply comes back as upstream sent it, whether or not the recorder could read it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import partial
from typing import TYPE_CHECKING, Any, NoReturn

import httpx
from aiohttp import web

from evals.harness.evidence import LlmCall, Role, ToolCall

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

ROLE_FINGERPRINTS: tuple[tuple[Role, str], ...] = (
    ("system1", "You are Alfred's Reflex Engine"),
    ("system2", "You are Alfred — personal butler"),
    ("librarian", "You are a memory analyst"),
    ("librarian", "You are a memory conflict resolver"),
    ("librarian", "Analyze these home assistant observations"),
    ("librarian", "You are a memory compressor"),
    ("librarian", "You are a pattern analyst"),
)
_DROP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
# Requests upstream at once, chat and passthrough alike. vLLM is shared with production:
# with the judge's JUDGE_CONNECTIONS, an eval run never has more than 4 there at once.
MAX_UPSTREAM = 2


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(text for p in _list(content) if isinstance(text := _obj(p).get("text"), str))


def classify_role(messages: list[dict[str, Any]]) -> Role:
    first = _text(messages[0].get("content")) if messages else ""
    for role, fingerprint in ROLE_FINGERPRINTS:
        if fingerprint in first:
            return role
    return "unknown"


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not JSON")


def _count(value: Any) -> int | None:
    """A token count, or None for anything that is not one (bools included)."""
    return value if type(value) is int and value >= 0 else None


def _parse(content: bytes) -> tuple[str | None, list[ToolCall], int | None, int | None]:
    """What a completion said. A reply that is not a JSON object reads as empty."""
    try:
        payload = _obj(json.loads(content))
    except (ValueError, RecursionError):
        payload = {}
    choices = _list(payload.get("choices")) or [{}]
    message = _obj(_obj(choices[0]).get("message"))
    calls: list[ToolCall] = []
    for tc in _list(message.get("tool_calls")):
        if not isinstance(tc, dict):
            continue
        fn = _obj(tc.get("function"))
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            args = None
        arguments = args if isinstance(args, dict) else {"_raw": raw}
        # A name that is not a string reads as "", as tools_offered leaves one out.
        name = fn.get("name")
        calls.append(ToolCall(name=name if isinstance(name, str) else "", arguments=arguments))
    text = message.get("content")
    usage = _obj(payload.get("usage"))
    return (
        text if isinstance(text, str) else None,
        calls,
        _count(usage.get("prompt_tokens")),
        _count(usage.get("completion_tokens")),
    )


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": {"message": message}}, status=status)


def _mirror(upstream: httpx.Response) -> web.Response:
    """The upstream reply, keeping its media type (not its charset or other parameters)."""
    media_type = upstream.headers.get("content-type", "").split(";")[0].strip()
    return web.Response(
        body=upstream.content,
        status=upstream.status_code,
        content_type=media_type or "application/json",
    )


class LlmProxy:
    def __init__(
        self,
        upstream: str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_concurrency: int = MAX_UPSTREAM,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.host = host
        self._port = port
        self.calls: list[LlmCall] = []
        self._sem = asyncio.Semaphore(max_concurrency)
        # The stamps of chat completions not yet in ``calls``, taken when each request
        # arrived. A call is recorded when upstream answers, stamped with when it was sent,
        # so a reader of a window waits for the ones sent inside it (``wait_idle``).
        self._in_flight: list[float] = []
        # Set, then replaced, whenever a call is recorded or leaves flight, so every waiter
        # re-checks.
        self._landed = asyncio.Event()
        self._timeout_s = timeout_s
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._runner: web.AppRunner | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport)
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post("/v1/chat/completions", self._chat)
        app.router.add_route("*", "/{tail:.*}", self._passthrough)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self._port).start()
        self._port = int(self._runner.addresses[0][1])

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def calls_between(self, t0: float, t1: float) -> list[LlmCall]:
        return [c for c in self.calls if t0 <= c.t <= t1]

    @property
    def in_flight(self) -> int:
        """Chat completions received and not yet recorded (or refused)."""
        return len(self._in_flight)

    def in_flight_since(self, since: float) -> list[float]:
        """The stamps, oldest first, of the chat completions in flight that arrived at or
        after *since*."""
        return sorted(t for t in self._in_flight if t >= since)

    def record(self, call: LlmCall) -> None:
        """Log a chat completion upstream answered (or failed), stamped with when, and wake
        whoever waits."""
        self.calls.append(call.model_copy(update={"answered_at": time.monotonic()}))
        self._wake()

    def _wake(self) -> None:
        landed, self._landed = self._landed, asyncio.Event()
        landed.set()

    async def wait_for_call(
        self, since: float, timeout: float, wanted: Callable[[LlmCall], bool] | None = None
    ) -> bool:
        """Wait for a recorded call that arrived at or after *since* (one *wanted* accepts,
        when given: any other call does not end the wait). False after *timeout* s."""
        try:
            async with asyncio.timeout(timeout):
                while not any(c.t >= since and (wanted is None or wanted(c)) for c in self.calls):
                    await self._landed.wait()
        except TimeoutError:
            return False
        return True

    async def wait_idle(self, since: float, timeout: float) -> bool:
        """Wait until no chat completion that arrived at or after *since* is in flight, so
        ``calls`` holds every one a window starting at *since* can hold. One from before
        *since* (say, left upstream by a container a restart killed) never enters that
        window, so it is not waited on. False when one is still in flight after *timeout*
        seconds."""
        try:
            async with asyncio.timeout(timeout):
                while self.in_flight_since(since):
                    await self._landed.wait()
        except TimeoutError:
            return False
        return True

    def _headers(self, request: web.Request) -> dict[str, str]:
        return {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}

    async def _chat(self, request: web.Request) -> web.Response:
        # In flight from the first byte, not the last: a call whose body is still being
        # read is already one a window must wait for.
        t = time.monotonic()
        self._in_flight.append(t)
        try:
            return await self._take(request, t)
        finally:  # recorded, refused with a 400, or the client gone
            self._in_flight.remove(t)
            self._wake()

    async def _take(self, request: web.Request, t: float) -> web.Response:
        """Read, check and forward one chat completion that arrived at *t*."""
        assert self._client is not None
        raw = await request.read()
        try:
            body: Any = json.loads(raw, parse_constant=_reject_constant)
        except ValueError as exc:
            return _error(400, f"the request body is not JSON: {exc}")
        except RecursionError:
            return _error(400, "the request body is nested too deeply to read")
        if not isinstance(body, dict):
            return _error(400, "the request body is not a JSON object")
        if body.get("stream"):
            return _error(400, "the alfred evals proxy does not support streaming")
        messages = body.get("messages", [])
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            return _error(400, "messages must be a list of objects")
        tools = [
            name
            for tool in _list(body.get("tools"))
            if isinstance(name := _obj(_obj(tool).get("function")).get("name"), str)
        ]
        call_for = partial(
            LlmCall,
            t=t,
            role=classify_role(messages),
            messages=messages,
            tools_offered=tools,
        )
        return await self._forward_and_record(request, raw, call_for)

    async def _forward_and_record(
        self, request: web.Request, raw: bytes, call_for: partial[LlmCall]
    ) -> web.Response:
        assert self._client is not None
        async with self._sem:
            started = time.monotonic()
            try:
                upstream = await self._client.post(
                    f"{self.upstream}/v1/chat/completions",
                    content=raw,
                    headers=self._headers(request),
                )
            except httpx.HTTPError as exc:
                self.record(call_for(latency_ms=(time.monotonic() - started) * 1000, status=502))
                return _error(502, f"upstream failed: {exc}")
        latency_ms = (time.monotonic() - started) * 1000
        try:
            text, tool_calls, prompt_tokens, completion_tokens = _parse(upstream.content)
            self.record(
                call_for(
                    latency_ms=latency_ms,
                    status=upstream.status_code,
                    response_text=text,
                    tool_calls=tool_calls,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            )
        except Exception:
            logger.exception("the recorder could not read a chat completion; passing it on as is")
        return _mirror(upstream)

    async def _passthrough(self, request: web.Request) -> web.Response:
        assert self._client is not None
        content = await request.read()
        async with self._sem:  # unrecorded, but still a share of vLLM
            try:
                upstream = await self._client.request(
                    request.method,
                    f"{self.upstream}{request.rel_url}",
                    content=content,
                    headers=self._headers(request),
                )
            except httpx.HTTPError as exc:
                return _error(502, f"upstream failed: {exc}")
        return _mirror(upstream)
