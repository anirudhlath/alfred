"""An OpenAI-compatible pass-through to vLLM that records every chat completion.

System 2 sends its tool calls to the model and to home-service, never onto a stream, so
this is the only place their arguments can be seen. Roles are told apart by the first
message's text (``ROLE_FINGERPRINTS``); a test pins those strings to the prompt sources.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from aiohttp import web

from evals.harness.evidence import LlmCall, Role, ToolCall

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


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def classify_role(messages: list[dict[str, Any]]) -> Role:
    first = _text(messages[0].get("content")) if messages else ""
    for role, fingerprint in ROLE_FINGERPRINTS:
        if fingerprint in first:
            return role
    return "unknown"


def _parse(payload: dict[str, Any]) -> tuple[str | None, list[ToolCall], int | None, int | None]:
    choices = payload.get("choices") or [{}]
    message = choices[0].get("message") or {}
    calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw}
        calls.append(ToolCall(name=str(fn.get("name", "")), arguments=args))
    usage = payload.get("usage") or {}
    return message.get("content"), calls, usage.get("prompt_tokens"), usage.get("completion_tokens")


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
        max_concurrency: int = 4,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.host = host
        self._port = port
        self.calls: list[LlmCall] = []
        self._sem = asyncio.Semaphore(max_concurrency)
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

    def _headers(self, request: web.Request) -> dict[str, str]:
        return {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}

    async def _chat(self, request: web.Request) -> web.Response:
        assert self._client is not None
        try:
            body: Any = json.loads(await request.read())
        except ValueError:
            return _error(400, "the request body is not JSON")
        if not isinstance(body, dict):
            return _error(400, "the request body is not a JSON object")
        if body.get("stream"):
            return _error(400, "the alfred evals proxy does not support streaming")
        messages = list(body.get("messages") or [])
        tools = [str(t.get("function", {}).get("name", "")) for t in body.get("tools") or []]
        t = time.monotonic()
        async with self._sem:
            started = time.monotonic()
            try:
                upstream = await self._client.post(
                    f"{self.upstream}/v1/chat/completions",
                    json=body,
                    headers=self._headers(request),
                )
            except httpx.HTTPError as exc:
                self.calls.append(
                    LlmCall(
                        t=t,
                        role=classify_role(messages),
                        latency_ms=(time.monotonic() - started) * 1000,
                        status=502,
                        messages=messages,
                        tools_offered=tools,
                    )
                )
                return _error(502, f"upstream failed: {exc}")
        latency_ms = (time.monotonic() - started) * 1000
        try:
            payload = upstream.json()
        except ValueError:
            payload = {}
        text, tool_calls, prompt_tokens, completion_tokens = _parse(payload)
        self.calls.append(
            LlmCall(
                t=t,
                role=classify_role(messages),
                latency_ms=latency_ms,
                status=upstream.status_code,
                messages=messages,
                tools_offered=tools,
                response_text=text,
                tool_calls=tool_calls,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
        return _mirror(upstream)

    async def _passthrough(self, request: web.Request) -> web.Response:
        assert self._client is not None
        content = await request.read()
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
