"""An in-memory stand-in for Langfuse's prompts API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.prompts.langfuse import LangfusePrompt

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence


class FakeLangfuse:
    """Serves *texts* by name at version *version*; a missing name is unseeded (None).

    ``error`` makes every read raise it. ``gate`` (when set) holds every read until it is
    set. ``created`` / ``labelled`` record the admin calls.
    """

    def __init__(
        self,
        texts: dict[str, str] | None = None,
        *,
        version: int = 7,
        prompt_type: str = "text",
        labels: tuple[str, ...] = ("production", "latest"),
    ) -> None:
        self.texts = dict(texts or {})
        self.version = version
        self.prompt_type = prompt_type
        self.labels = labels
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.calls: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, list[str]]] = []
        self.labelled: list[tuple[str, int, list[str]]] = []
        self.closed = False

    async def get(self, name: str, *, label: str) -> LangfusePrompt | None:
        self.calls.append((name, label))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        text = self.texts.get(name)
        if text is None:
            return None
        chat = self.prompt_type != "text"
        return LangfusePrompt(
            name, self.version, self.prompt_type, None if chat else text, self.labels
        )

    async def create(
        self, name: str, text: str, *, labels: Sequence[str], commit_message: str
    ) -> LangfusePrompt:
        self.version += 1
        self.texts[name] = text
        self.created.append((name, text, list(labels)))
        return LangfusePrompt(name, self.version, "text", text, (*labels, "latest"))

    async def set_labels(self, name: str, version: int, labels: Sequence[str]) -> LangfusePrompt:
        self.labelled.append((name, version, list(labels)))
        return LangfusePrompt(name, version, "text", self.texts[name], (*labels, "latest"))

    async def aclose(self) -> None:
        self.closed = True
