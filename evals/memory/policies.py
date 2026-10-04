"""Decay policies the eval compares — a registry, so a fix lands as one more entry.

Every policy here runs the Librarian's real ``_apply_decay`` (pressure filter,
entity+date grouping, compression and ``copy_to_cold_and_remove``) and differs only
in the two things a decay design chooses: where candidates come from and the
threshold. A future policy that changes the formula or the migration itself
subclasses ``DecayPolicy`` directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from core.memory.context_index import ContextIndexManager
from evals.memory.env import scan_hot
from evals.memory.metrics import is_eligible

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import Range, SearchResult, VectorStore
    from evals.memory.env import SimEnv
    from shared.types import AioRedis

# What master ships: the threshold every pressure value sits at or below.
MASTER_THRESHOLD = 1.0


@dataclass(frozen=True)
class PassStats:
    migrated: int
    candidates: int
    candidates_episodic: int
    candidates_eligible: int


class DecayPolicy(ABC):
    name: ClassVar[str]
    summary: ClassVar[str]

    def __init__(self, env: SimEnv, threshold: float) -> None:
        self._env = env
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    @abstractmethod
    async def run_pass(self, now: datetime) -> PassStats: ...


POLICIES: dict[str, type[DecayPolicy]] = {}


def register_policy[P: type[DecayPolicy]](cls: P) -> P:
    POLICIES[cls.name] = cls
    return cls


# ---------------------------------------------------------------------------
# Candidate sources
# ---------------------------------------------------------------------------


class RecordingIndex(ContextIndexManager):
    """The real context index, remembering what it last handed the decay pass."""

    last: list[SearchResult]

    def __init__(self, store: VectorStore, embedder: EmbeddingProvider) -> None:
        super().__init__(store, embedder)
        self.last = []

    async def select(self, where: Mapping[str, Range]) -> list[SearchResult]:
        self.last = await super().select(where)
        return self.last


class ScanAllHotIndex(RecordingIndex):
    """Hands the decay pass every hot entry, found by SCAN — eval-only.

    The ranges are ignored on purpose: this is what "selection is perfect" means, so
    whatever still goes wrong is the formula's.
    """

    def __init__(self, store: VectorStore, embedder: EmbeddingProvider, redis: AioRedis) -> None:
        super().__init__(store, embedder)
        self._redis = redis

    async def select(self, where: Mapping[str, Range]) -> list[SearchResult]:
        self.last = await scan_hot(self._redis)
        return self.last


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


class LibrarianDecay(DecayPolicy):
    """Runs ``Librarian._apply_decay`` against a candidate source and a threshold."""

    def __init__(self, env: SimEnv, threshold: float) -> None:
        super().__init__(env, self.effective_threshold(threshold))
        self._index = self.candidate_source(env)
        self._librarian = env.librarian(self._index)

    def effective_threshold(self, configured: float) -> float:
        return configured

    def candidate_source(self, env: SimEnv) -> RecordingIndex:
        return RecordingIndex(env.hot, env.embedder)

    async def run_pass(self, now: datetime) -> PassStats:
        migrated = await self._librarian._apply_decay(
            decay_migration_threshold=self._threshold, now=now
        )
        seen = self._index.last
        now_ts = now.timestamp()
        return PassStats(
            migrated=migrated,
            candidates=len(seen),
            candidates_episodic=sum(1 for r in seen if r.metadata.type == "episodic"),
            candidates_eligible=sum(1 for r in seen if is_eligible(r, now_ts, self._threshold)),
        )


@register_policy
class NoDecay(LibrarianDecay):
    name = "no_decay"
    summary = (
        "master: the same pass at threshold 1.0, which no pressure exceeds — nothing ever "
        "leaves hot"
    )

    def effective_threshold(self, configured: float) -> float:
        return MASTER_THRESHOLD


@register_policy
class BranchDecay(LibrarianDecay):
    name = "branch"
    summary = (
        "this branch's _apply_decay: candidates selected by metadata ranges that cover "
        "every memory the formula could move, threshold 0.2"
    )


@register_policy
class OracleDecay(LibrarianDecay):
    name = "oracle"
    summary = (
        "eval-only: exact pressure for every hot memory (SCAN), migrate all above the "
        "threshold — the formula with perfect selection"
    )

    def candidate_source(self, env: SimEnv) -> RecordingIndex:
        return ScanAllHotIndex(env.hot, env.embedder, env.redis)


def policy_factory(name: str) -> Callable[[SimEnv, float], DecayPolicy]:
    try:
        return POLICIES[name]
    except KeyError:
        raise ValueError(f"unknown policy {name!r}; known: {', '.join(POLICIES)}") from None
