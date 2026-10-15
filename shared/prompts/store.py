"""Where prompt text comes from: Langfuse, then its last copy in Redis, then the repo.

Reads are served from memory. An entry older than the TTL is still served while one
background task per prompt refreshes it, so a turn never waits on Langfuse once a
process has warmed up. A cold read waits for the fetch, which a 2 s client timeout
bounds, and whatever it ends up serving (a fallback included) is cached for the TTL, so
an outage costs one wait per prompt, not one per turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from shared.config import DEFAULT_PROMPT_CACHE_TTL_S, DEFAULT_PROMPT_LABEL
from shared.prompts.langfuse import LangfuseClient
from shared.prompts.template import (
    PromptSource,
    RenderedPrompt,
    check_variables,
    render,
    repo_text,
    unusable_reason,
)
from shared.streams import PROMPT_COPY_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from shared.config import AlfredConfig
    from shared.prompts.langfuse import PromptFetcher
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

# Redis is only a fallback and a copy; neither may hold a read for longer than this.
_REDIS_TIMEOUT_S = 0.5


@dataclass(frozen=True)
class _Template:
    text: str
    version: int | None
    source: PromptSource


@dataclass(frozen=True)
class _Entry:
    template: _Template
    fetched_at: float


def _repo(name: str) -> _Template:
    return _Template(repo_text(name), None, "repo")


class PromptStore:
    """Serves rendered prompts; with no Langfuse client, the repo defaults only."""

    def __init__(
        self,
        *,
        langfuse: PromptFetcher | None = None,
        redis: AioRedis | None = None,
        label: str = DEFAULT_PROMPT_LABEL,
        ttl_s: float = DEFAULT_PROMPT_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._langfuse = langfuse
        self._redis = redis
        self._label = label
        self._ttl_s = ttl_s
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Task[_Template]] = {}
        self._failing: set[str] = set()
        self._closed = False

    @classmethod
    def from_config(cls, config: AlfredConfig, *, redis: AioRedis | None) -> PromptStore:
        """Langfuse-backed when ``LANGFUSE_HOST`` and both keys are set, else repo-only."""
        if not config.langfuse_host:
            return cls()
        missing = [
            name
            for name, value in (
                ("LANGFUSE_PUBLIC_KEY", config.langfuse_public_key),
                ("LANGFUSE_SECRET_KEY", config.langfuse_secret_key),
            )
            if not value
        ]
        if missing:
            logger.warning(
                "LANGFUSE_HOST is set but %s is not; serving the repo prompts",
                " and ".join(missing),
            )
            return cls()
        try:
            client = LangfuseClient(
                config.langfuse_host,
                public_key=config.langfuse_public_key,
                secret_key=config.langfuse_secret_key,
            )
        except httpx.InvalidURL:
            # The value is not echoed: a host may carry userinfo.
            logger.warning("LANGFUSE_HOST is not a valid URL; serving the repo prompts")
            return cls()
        return cls(
            langfuse=client, redis=redis, label=config.prompt_label, ttl_s=config.prompt_cache_ttl_s
        )

    def describe(self) -> str:
        """Where prompts come from, for the startup log."""
        if self._langfuse is None:
            return "repo defaults"
        return f"Langfuse, label {self._label}"

    async def get(self, name: str, **variables: object) -> RenderedPrompt:
        """Prompt *name* rendered with *variables*, which must be exactly its declared set.

        Raises:
            ValueError: *name* is unknown or *variables* are not its declared set.
        """
        check_variables(name, variables)
        template = await self._template(name)
        return RenderedPrompt(
            render(template.text, variables), name, template.version, template.source
        )

    async def aclose(self) -> None:
        """Cancel in-flight refreshes and close the Langfuse client."""
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._langfuse is not None:
            await self._langfuse.aclose()

    async def _template(self, name: str) -> _Template:
        if self._langfuse is None:
            return _repo(name)
        entry = self._cache.get(name)
        if self._closed:
            return entry.template if entry is not None else _repo(name)
        if entry is None:
            # Shielded: a reader that gives up must not cancel the fetch others await.
            return await asyncio.shield(self._refresh(name))
        if self._clock() - entry.fetched_at >= self._ttl_s:
            self._refresh(name)
        return entry.template

    def _refresh(self, name: str) -> asyncio.Task[_Template]:
        """The one fetch in flight for *name*, started if there is none."""
        task = self._inflight.get(name)
        if task is None:
            task = asyncio.create_task(self._fetch(name), name=f"prompt-refresh:{name}")
            self._inflight[name] = task
            task.add_done_callback(lambda _: self._inflight.pop(name, None))
        return task

    async def _fetch(self, name: str) -> _Template:
        assert self._langfuse is not None
        try:
            remote = await self._langfuse.get(name, label=self._label)
        except Exception as exc:  # nothing may escape a fallback
            self._note_failure(name, exc)
            template = await self._fallback(name)
        else:
            self._note_recovery(name)
            if remote is None:
                template = _repo(name)  # not seeded yet: normal before the first push
            elif (reason := unusable_reason(name, remote.type, remote.text)) is not None:
                logger.warning(
                    "Langfuse prompt %s v%d %s; serving the repo default",
                    name,
                    remote.version,
                    reason,
                )
                template = _repo(name)
            else:
                assert remote.text is not None
                template = _Template(remote.text, remote.version, "langfuse")
        self._cache[name] = _Entry(template, self._clock())
        if template.source == "langfuse":
            await self._save_copy(name, template)
        return template

    async def _fallback(self, name: str) -> _Template:
        """What to serve when Langfuse failed: the last good text, then Redis, then the repo."""
        cached = self._cache.get(name)
        if cached is not None and cached.template.source != "repo":
            return cached.template
        return await self._copy(name) or _repo(name)

    def _key(self, name: str) -> str:
        return PROMPT_COPY_KEY.format(name=name, label=self._label)

    async def _copy(self, name: str) -> _Template | None:
        """The last Langfuse text fetched for *name*, if Redis has a usable one."""
        if self._redis is None:
            return None
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_S):
                raw = await self._redis.get(self._key(name))
            if raw is None:
                return None
            data = json.loads(raw)
            text, version = data["text"], data["version"]
        except Exception as exc:  # a fallback must never raise
            logger.debug("No usable Redis copy of prompt %s: %r", name, exc)
            return None
        if not isinstance(text, str) or not isinstance(version, int):
            return None
        if unusable_reason(name, "text", text) is not None:
            return None
        return _Template(text, version, "redis")

    async def _save_copy(self, name: str, template: _Template) -> None:
        if self._redis is None:
            return
        payload = json.dumps({"text": template.text, "version": template.version})
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_S):
                await self._redis.set(self._key(name), payload)
        except Exception as exc:  # a copy that is not saved is not an error
            logger.debug("Could not save the Redis copy of prompt %s: %r", name, exc)

    def _note_failure(self, name: str, exc: Exception) -> None:
        if name in self._failing:
            logger.debug("Langfuse still unreachable for prompt %s: %s", name, exc)
            return
        self._failing.add(name)
        logger.warning(
            "Langfuse unreachable for prompt %s (%s); serving the last copy or the repo default",
            name,
            exc,
        )

    def _note_recovery(self, name: str) -> None:
        if name in self._failing:
            self._failing.discard(name)
            logger.info("Langfuse reachable again for prompt %s", name)
