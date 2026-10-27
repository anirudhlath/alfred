"""Langfuse's prompts API, the three calls Alfred makes: read a label, create a version,
move labels.

Basic auth with the project's public and secret keys. Every failure is a
``LangfuseError`` except a read of a prompt that does not exist, which is None: an
unseeded prompt is normal before the first ``alfred prompts push``.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_LANGFUSE_TIMEOUT_S: Final = 2.0
_PROMPTS: Final = "/api/public/v2/prompts"


class LangfuseError(RuntimeError):
    """Langfuse could not be reached, refused the request or answered nonsense."""


@dataclass(frozen=True)
class LangfusePrompt:
    """One prompt version; ``text`` is None for a chat prompt."""

    name: str
    version: int
    type: str
    text: str | None
    labels: tuple[str, ...]


class PromptFetcher(Protocol):
    async def get(self, name: str, *, label: str) -> LangfusePrompt | None: ...

    async def aclose(self) -> None: ...


class PromptAdmin(PromptFetcher, Protocol):
    async def create(
        self, name: str, text: str, *, labels: Sequence[str], commit_message: str
    ) -> LangfusePrompt: ...

    async def set_labels(
        self, name: str, version: int, labels: Sequence[str]
    ) -> LangfusePrompt: ...


class LangfuseClient:
    """Async client for Langfuse's public prompts API."""

    def __init__(
        self,
        host: str,
        *,
        public_key: str,
        secret_key: str,
        timeout_s: float = DEFAULT_LANGFUSE_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        # Never let these into an error: the secret itself and the Basic token built from it.
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._redact = tuple(v for v in (token, secret_key) if v)
        self._http = httpx.AsyncClient(
            base_url=host.rstrip("/"),
            auth=(public_key, secret_key),
            timeout=timeout_s,
            transport=transport,
        )

    async def get(self, name: str, *, label: str) -> LangfusePrompt | None:
        """The version of *name* carrying *label*, or None if there is none."""
        response = await self._request("GET", _path(name), params={"label": label})
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        return _prompt(_json(response))

    async def create(
        self, name: str, text: str, *, labels: Sequence[str], commit_message: str
    ) -> LangfusePrompt:
        """A new text version of *name*; Langfuse adds the ``latest`` label itself."""
        body = {
            "type": "text",
            "name": name,
            "prompt": text,
            "labels": list(labels),
            "config": {},
            "commitMessage": commit_message,
        }
        return _prompt(_json(await self._request("POST", _PROMPTS, json=body)))

    async def set_labels(self, name: str, version: int, labels: Sequence[str]) -> LangfusePrompt:
        """Put *labels* on *version*; Langfuse moves each label off any other version."""
        path = f"{_path(name)}/versions/{version}"
        body = {"newLabels": list(labels)}
        return _prompt(_json(await self._request("PATCH", path, json=body)))

    async def aclose(self) -> None:
        await self._http.aclose()

    def _scrub(self, exc: Exception) -> str:
        text = str(exc)
        for secret in self._redact:
            text = text.replace(secret, "***")
        return text

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            # httpx's timeout is per phase; this is the total deadline.
            async with asyncio.timeout(self._timeout_s):
                response = await self._http.request(method, path, params=params, json=json)
        except TimeoutError as exc:
            raise LangfuseError(f"{method} {path}: no answer within {self._timeout_s}s") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # ``from None``: the original's text may carry request headers.
            raise LangfuseError(
                f"{method} {path}: {type(exc).__name__}: {self._scrub(exc)}"
            ) from None
        if method == "GET" and response.status_code == httpx.codes.NOT_FOUND:
            return response
        if response.is_error:
            raise LangfuseError(f"{method} {path}: HTTP {response.status_code}")
        return response


def _path(name: str) -> str:
    return f"{_PROMPTS}/{quote(name, safe='')}"


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise LangfuseError(f"Langfuse answered HTTP {response.status_code} without JSON") from exc


def _prompt(data: Any) -> LangfusePrompt:
    try:
        prompt = data["prompt"]
        return LangfusePrompt(
            name=str(data["name"]),
            version=int(data["version"]),
            type=str(data["type"]),
            text=prompt if isinstance(prompt, str) else None,
            labels=tuple(str(label) for label in data.get("labels", [])),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LangfuseError(f"unexpected prompt shape: {exc!r}") from exc
