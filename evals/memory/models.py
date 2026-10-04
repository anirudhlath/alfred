"""Result models for the memory-decay eval (saved as one JSON per run)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import Literal

from pydantic import BaseModel, computed_field

from evals.memory.dataset import Category  # noqa: TC001 — pydantic field type


class Ratio(BaseModel):
    hits: int
    n: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rate(self) -> float | None:
        return self.hits / self.n if self.n else None


class PassRecord(BaseModel):
    """One decay pass: what it cost and what its selection step saw."""

    hour: int
    wall_ms: float
    embed_calls: int
    embed_ms: float
    migrated: int
    # What the selection step handed the pressure filter, and how much of it could move.
    candidates: int
    candidates_episodic: int
    candidates_eligible: int


class DaySnapshot(BaseModel):
    """The hot store at the end of a simulated day, read by SCAN."""

    day: int
    hot_episodic: int
    hot_by_category: dict[str, int]
    # Identical texts embed to identical vectors, which degrade HNSW navigation.
    hot_distinct_content: int
    hot_distinct_semantic_keys: int
    cold_entries: int
    # Hot episodic memories whose exact pressure is above the configured threshold right
    # now (for no_decay: what a working decay pass would move).
    eligible_in_hot: int
    # ...and that were already above it at the previous daily check (>= 24 passes).
    stuck_1d: int
    # ...at the last 8 daily checks (>= 7 days of passes).
    stuck_7d: int
    noise_older_14d: int
    noise_older_14d_gone: int


class ProbeOutcome(BaseModel):
    question: str
    target_id: str
    category: Category
    location: Literal["hot", "cold", "missing"]
    # Involuntary recall exactly as the Conscious Engine runs it before every reply.
    involuntary_hit: bool
    involuntary_rank: int | None
    involuntary_returned: int
    involuntary_noise: int
    # Exact brute-force KNN over the hot store's own vectors: what a perfect search
    # returns. exact_hit applies the same limit and floor as involuntary recall.
    exact_rank: int | None
    exact_score: float | None
    exact_hit: bool
    # The same search with the similarity floor at 0 — separates "ranked too low"
    # from "under the threshold".
    open_hit: bool
    open_rank: int | None
    target_score: float | None
    # Deliberate recall: the memory_recall_memories tool (hot only, today).
    tool_hit: bool
    # EpisodicMemory.recall — hot + cold, the path spec §6.2/§7.4 describes.
    recall_hit: bool
    # The cold store searched alone (sqlite-vec, exact KNN): whether the archive holds
    # the answer at all, whatever the hot+cold merge then does with it. cold_score is
    # the cold store's own score for the target, on that store's scale.
    cold_hit: bool
    cold_score: float | None


class Checkpoint(BaseModel):
    day: int
    probes: list[ProbeOutcome]
    kept_significant: Ratio
    kept_recalled: Ratio
    snapshot: DaySnapshot
    # Problem 2: hot memories at negative cosine to the decay pass's placeholder query,
    # which its min_similarity=0.0 floor can therefore never return.
    eligible_unpickable: Ratio
    stuck_unpickable: Ratio
    retrievals_applied: int
    retrievals_lost: int


class PolicyResult(BaseModel):
    name: str
    summary: str
    threshold: float
    wall_seconds: float
    write_embed_calls: int
    decay_embed_calls: int
    decay_embed_seconds: float
    suppressed_stat_writes: int
    passes: list[PassRecord]
    days: list[DaySnapshot]
    checkpoints: list[Checkpoint]


class RunSettings(BaseModel):
    seed: int
    days: int
    pass_every_hours: int
    measure_days: list[int]
    threshold: float
    embedding_model: str
    embedding_backend: str
    embedding_dim: int
    involuntary_recall_limit: int
    involuntary_recall_threshold: float
    redis_image: str
    redis_version: str = ""


class MemoryEvalRun(BaseModel):
    run_id: str
    timestamp: datetime
    settings: RunSettings
    dataset_counts: dict[str, int]
    probe_count: int
    policies: list[PolicyResult]
