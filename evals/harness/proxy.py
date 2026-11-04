"""An OpenAI-compatible pass-through to vLLM that records every chat completion.

System 2 sends its tool calls to the model and to home-service, never onto a stream, so
this is the only place their arguments can be seen. A call's role is its ``X-Alfred-Role``
header (``shared/llm_roles.py``), which every LLM request Alfred makes carries; a call
without a recognised one is ``unknown``. The prompt's text plays no part, so a prompt
edit cannot break attribution.

The recorder never changes a reply: a chat request goes upstream as the bytes Alfred sent,
and the reply comes back as upstream sent it, whether or not the recorder could read it.

Where a chat request goes depends on its role. System 1's can go to an upstream of its own
(``reflex_upstream``, a hosted OpenAI-compatible server) and carry that upstream's API key;
every other role, and every request that is not a chat completion, goes to ``upstream``.
The key is only ever put on the request to the reflex upstream: it is not recorded, not
logged, and not quoted in an error. A key must be fit to be a header value, or the proxy
will not start: an HTTP library that refuses a header quotes it, and the quote reaches
whoever reads the error. For the same reason a request to the reflex upstream that fails is
answered with the failure's class alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, NoReturn

import httpx
from aiohttp import web

from evals.harness.evidence import LlmCall, Role, ToolCall
from evals.harness.preflight import header_safe
from shared.llm_roles import APPROVAL, LIBRARIAN, ROLE_HEADER, SYSTEM1, SYSTEM2

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Alfred's roles, by the X-Alfred-Role value each sends. A test pins it to LLM_ROLES.
ROLES_BY_HEADER: dict[str, Role] = {
    SYSTEM1: "system1",
    SYSTEM2: "system2",
    LIBRARIAN: "librarian",
    APPROVAL: "approval",
}
_DROP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
# Requests upstream at once, chat and passthrough alike. vLLM is shared with production:
# with the judge's JUDGE_CONNECTIONS, an eval run never has more than 4 there at once.
MAX_UPSTREAM = 2


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def role_of(header: str | None) -> Role:
    """The role an ``X-Alfred-Role`` value names; anything else, or none, is unknown."""
    return ROLES_BY_HEADER.get(header or "", "unknown")


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


@dataclass(eq=False)  # each one is its own request, whatever its fields
class _Flight:
    """A chat completion received and not yet recorded: when it arrived, and whose it is
    once its body has been read (None until then)."""

    t: float
    role: Role | None = None


class LlmProxy:
    def __init__(
        self,
        upstream: str,
        *,
        reflex_upstream: str | None = None,
        reflex_api_key: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        max_concurrency: int = MAX_UPSTREAM,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if reflex_api_key and not reflex_upstream:
            raise ValueError("reflex_api_key needs a reflex_upstream: a key is never sent to vLLM")
        if reflex_api_key and not header_safe(reflex_api_key):
            # Never say which character: the message must hold nothing of the key.
            raise ValueError("reflex_api_key is not a usable header value")
        self.upstream = upstream.rstrip("/")
        # System 1's chat completions go here: ``upstream`` unless a reflex upstream is given.
        self._reflex_upstream = (reflex_upstream or upstream).rstrip("/")
        # Whether System 1 has an upstream of its own, whose failures are reported by class.
        self._reflex_apart = bool(reflex_upstream)
        # Sent to the reflex upstream and nowhere else; empty is no key at all.
        self._reflex_key = reflex_api_key or None
        self.host = host
        self._port = port
        self.calls: list[LlmCall] = []
        self._sem = asyncio.Semaphore(max_concurrency)
        # The chat completions not yet in ``calls``, stamped when each request arrived. A
        # call is recorded when upstream answers, stamped with when it was sent, so a reader
        # of a window waits for the ones sent inside it (``wait_idle``).
        self._in_flight: list[_Flight] = []
        # Set, then replaced, whenever a call is recorded, leaves flight or is found to be
        # one role's, so every waiter re-checks.
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

    def in_flight_since(self, since: float, role: Role | None = None) -> list[float]:
        """The stamps, oldest first, of the chat completions in flight that arrived at or
        after *since*. With *role*, only that role's, and those whose body is still being
        read: they could be anyone's."""
        return sorted(
            f.t
            for f in self._in_flight
            if f.t >= since and (role is None or f.role in (None, role))
        )

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

    async def wait_idle(self, since: float, timeout: float, role: Role | None = None) -> bool:
        """Wait until no chat completion that arrived at or after *since* is in flight, so
        ``calls`` holds every one a window starting at *since* can hold. One from before
        *since* (say, left upstream by a container a restart killed) never enters that
        window, so it is not waited on. With *role*, only that role's calls are waited on
        (see ``in_flight_since``). False when one is still in flight after *timeout*
        seconds."""
        try:
            async with asyncio.timeout(timeout):
                while self.in_flight_since(since, role):
                    await self._landed.wait()
        except TimeoutError:
            return False
        return True

    def _headers(self, request: web.Request, key: str | None = None) -> dict[str, str]:
        """The headers Alfred sent, less the hop-by-hop ones. With *key*, it takes the place
        of any Authorization header Alfred sent."""
        drop = _DROP_HEADERS if key is None else _DROP_HEADERS | {"authorization"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in drop}
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _failure(self, exc: Exception, *, to_reflex: bool) -> str:
        """What went wrong upstream. For the reflex upstream, the exception's class and
        nothing it says: an HTTP library quotes the header value it refuses to send, with
        control characters escaped, so no search for the key in its words would find it."""
        return type(exc).__name__ if to_reflex and self._reflex_apart else str(exc)

    async def _chat(self, request: web.Request) -> web.Response:
        # In flight from the first byte, not the last: a call whose body is still being
        # read is already one a window must wait for.
        flight = _Flight(t=time.monotonic())
        self._in_flight.append(flight)
        try:
            return await self._take(request, flight)
        finally:  # recorded, refused with a 400, or the client gone
            self._in_flight.remove(flight)
            self._wake()

    async def _take(self, request: web.Request, flight: _Flight) -> web.Response:
        """Read, check and forward one chat completion, in flight as *flight*."""
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
        role = flight.role = role_of(request.headers.get(ROLE_HEADER))
        self._wake()  # a waiter on another role's calls need not wait on this one
        call_for = partial(
            LlmCall,
            t=flight.t,
            role=role,
            request_body=body,
            messages=messages,
            tools_offered=tools,
        )
        return await self._forward_and_record(request, raw, call_for, role)

    async def _forward_and_record(
        self, request: web.Request, raw: bytes, call_for: partial[LlmCall], role: Role
    ) -> web.Response:
        assert self._client is not None
        to_reflex = role == "system1"
        async with self._sem:
            started = time.monotonic()
            try:
                upstream = await self._client.post(
                    f"{self._reflex_upstream if to_reflex else self.upstream}/v1/chat/completions",
                    content=raw,
                    headers=self._headers(request, self._reflex_key if to_reflex else None),
                )
            except httpx.HTTPError as exc:
                self.record(call_for(latency_ms=(time.monotonic() - started) * 1000, status=502))
                return _error(502, f"upstream failed: {self._failure(exc, to_reflex=to_reflex)}")
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
