"""Librarian — nightly consolidation of scratchpad into structured memory.

The Librarian replays the scratchpad, extracts episodic entries,
updates semantic memory (preferences + profile), detects patterns
for procedural memory, and applies decay to old entries.

Uses Claude for LLM-powered extraction and conflict resolution.
Atomic file writes: write to .tmp then os.rename() for memory files.
On failure, the scratchpad accumulates — memory files never left partial.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel

from core.memory.schemas import EpisodicEntry, RoutineSpec, RoutineStep, SignificanceScore
from shared.streams import SCRATCHPAD_QUEUE

if TYPE_CHECKING:
    from core.memory.context_index import ContextIndexManager
    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.routines.store import RoutineStore
    from core.memory.significance import SignificanceScorer
    from core.memory.vector_store import SearchResult
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Semantic conflict resolution models
# ---------------------------------------------------------------------------


class ConflictItem(BaseModel):
    """A single item in the conflict resolution LLM response."""

    type: Literal["confirm", "contradict", "new"]
    # Used by "confirm" and "contradict" (the existing preference line)
    line: str = ""
    # Used by "contradict" — the new value
    old: str = ""
    new: str = ""
    # Used by "new" — the content to append
    content: str = ""
    explanation: str = ""


_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
_DEFAULT_PREFERENCES_DIR = str(_MEMORY_DIR / "preferences")
_DEFAULT_PROFILE_DIR = str(_MEMORY_DIR / "profile")


def _group_by_entity_date(
    results: list[SearchResult],
) -> tuple[list[list[SearchResult]], list[SearchResult]]:
    """Group decayed entries by (shared_entity, date) for compression."""
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[SearchResult]] = defaultdict(list)
    ungrouped: list[SearchResult] = []

    for result in results:
        entities_str = result.metadata.entities
        if not entities_str:
            ungrouped.append(result)
            continue

        ts = result.metadata.timestamp
        date_str = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")

        entities = [e.strip() for e in entities_str.split(",") if e.strip()]
        if not entities:
            ungrouped.append(result)
            continue

        placed = False
        for entity in entities:
            key = (entity, date_str)
            if key in buckets:
                buckets[key].append(result)
                placed = True
                break
        if not placed:
            buckets[(entities[0], date_str)].append(result)

    groups: list[list[SearchResult]] = []
    for bucket in buckets.values():
        if len(bucket) >= 2:
            groups.append(bucket)
        else:
            ungrouped.extend(bucket)

    return groups, ungrouped


def _routine_index_content(routine: RoutineSpec) -> str:
    """Build the content string used to index a routine in the context store."""
    steps = "; ".join(s.description for s in routine.steps) if routine.steps else "N/A"
    return (
        f"Routine ({routine.state}): {routine.name} "
        f"— {routine.trigger_pattern}. "
        f"Steps: {steps}. "
        f"Confidence: {routine.confidence:.2f}"
    )


class Librarian:
    """Nightly consolidation agent.

    Drains the scratchpad queue, processes entries through an LLM,
    and writes to the three memory layers.
    """

    def __init__(
        self,
        redis: AioRedis,
        episodic_memory: EpisodicMemory,
        routine_store: RoutineStore,
        significance_scorer: SignificanceScorer,
        context_index: ContextIndexManager,
        preferences_dir: str = _DEFAULT_PREFERENCES_DIR,
        profile_dir: str = _DEFAULT_PROFILE_DIR,
        claude_api_key: str = "",
        claude_model: str = "openrouter/anthropic/claude-sonnet-4",
        conflict_min_observations: int = 5,
        conflict_min_days: int = 14,
        decay_migration_threshold: float = 1.0,
        pattern_min_occurrences: int = 3,
        pattern_min_days: int = 7,
        pattern_confidence_threshold: float = 0.6,
        routine_decay_per_cycle: float = 0.05,
        routine_archive_threshold: float = 0.3,
        routine_suggestion_cooldown_hours: int = 24,
    ) -> None:
        self._redis = redis
        self._episodic_memory = episodic_memory
        self._cold_store = episodic_memory._cold
        self._embedder = episodic_memory._embedder
        self._routines = routine_store
        self._scorer = significance_scorer
        self._context_index = context_index
        self._preferences_dir = Path(preferences_dir)
        self._profile_dir = Path(profile_dir)
        self._api_key = claude_api_key
        self._model = claude_model
        self._conflict_min_observations = conflict_min_observations
        self._conflict_min_days = conflict_min_days
        self._decay_migration_threshold = decay_migration_threshold
        self._pattern_min_occurrences = pattern_min_occurrences
        self._pattern_min_days = pattern_min_days
        self._pattern_confidence_threshold = pattern_confidence_threshold
        self._routine_decay_per_cycle = routine_decay_per_cycle
        self._routine_archive_threshold = routine_archive_threshold
        self._routine_suggestion_cooldown_hours = routine_suggestion_cooldown_hours
        self._indexed_routine_content: dict[str, str] = {}

    _PROCESSING_KEY = f"{SCRATCHPAD_QUEUE}:processing"

    async def _drain_scratchpad(self) -> list[str]:
        """Atomically drain the scratchpad queue.

        Uses RENAME to atomically swap the queue to a processing key,
        then drains the processing key. This prevents the race where
        new entries arrive between LRANGE and LTRIM and get silently lost.
        If the Librarian crashes mid-processing, the processing key
        survives for the next cycle to pick up.
        """
        # Check for leftover processing key from a previous crash
        leftover: list[bytes] = await self._redis.lrange(  # type: ignore[misc]
            self._PROCESSING_KEY, 0, -1
        )
        if not leftover:
            # Atomically move the queue to the processing key
            try:
                await self._redis.rename(SCRATCHPAD_QUEUE, self._PROCESSING_KEY)
            except Exception:
                # RENAME fails if the source key doesn't exist (empty queue)
                return []

        raw: list[bytes] = await self._redis.lrange(  # type: ignore[misc]
            self._PROCESSING_KEY, 0, -1
        )
        # NOTE: Do NOT delete the processing key here — it is deleted in
        # consolidate() after episodic writes succeed. This ensures crash
        # recovery: if we crash between read and write, entries survive.
        return [r.decode() if isinstance(r, bytes) else str(r) for r in raw]

    async def _analyse_batch(self, summaries: list[str]) -> list[dict[str, Any]]:
        """Richer single-pass LLM analysis: entities + significance + semantic key.

        Returns one dict per summary with keys:
          - ``entities``: list[str]
          - ``significance``: dict with keys safety/novelty/personal/emotional (0-1 floats)
          - ``semantic_key``: str — human-readable rewrite for vector indexing

        Falls back to empty dicts on any error.
        """
        if not self._api_key or not summaries:
            return [{} for _ in summaries]
        try:
            import litellm  # runtime import — optional dependency

            numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(summaries))
            system_prompt = (
                "You are a memory analyst for a home automation assistant. "
                "For each numbered observation, return a JSON array (one element per observation). "
                "Each element must have:\n"
                '  "entities": list of entity ids, room names, or people mentioned\n'
                '  "significance": object with float fields safety/novelty/personal/emotional '
                "(each 0.0-1.0, where 1.0 is most significant)\n"
                '  "semantic_key": short human-readable phrase summarising the core meaning '
                "(used for vector search)\n\n"
                "safety=1.0 for emergencies/alarms, novelty=1.0 for first-time events, "
                "personal=1.0 for direct user interactions, emotional=1.0 for strong sentiment.\n"
                "Return ONLY the JSON array with no surrounding text.\n"
                "Example for 2 observations:\n"
                '[\n  {"entities": ["light.living_room"], '
                '"significance": {"safety": 0.0, "novelty": 0.3, "personal": 0.5, '
                '"emotional": 0.2}, '
                '"semantic_key": "Evening lighting preference in living room"},\n'
                '  {"entities": ["climate.main"], '
                '"significance": {"safety": 0.0, "novelty": 0.0, "personal": 0.3, '
                '"emotional": 0.1}, '
                '"semantic_key": "Thermostat set to comfortable temperature"}\n]'
            )
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": numbered},
                ],
                max_tokens=max(300, 150 * len(summaries)),
                api_key=self._api_key,
            )
            raw = response.choices[0].message.content or "[]"
            parsed: list[dict[str, Any]] = json.loads(raw)
            # Pad/trim to match input length
            while len(parsed) < len(summaries):
                parsed.append({})
            return parsed[: len(summaries)]
        except Exception as exc:
            logger.warning("Batch analysis failed: %s", exc)
            return [{} for _ in summaries]

    async def _extract_episodic_entries(
        self, scratchpad_lines: list[str]
    ) -> list[tuple[EpisodicEntry, dict[str, Any]]]:
        """Extract episodic entries with LLM enrichment.

        Returns a list of ``(EpisodicEntry, llm_significance)`` tuples where
        ``llm_significance`` is the raw significance dict from the LLM (may be
        empty if no API key or on error).
        """
        parsed: list[tuple[str, str]] = []
        for line in scratchpad_lines:
            # Parse timestamp and source from scratchpad format:
            # "2026-03-19T10:00:00Z [reflex] action(...) -> result"
            parts = line.split("] ", 1)
            source = "unknown"
            summary = line
            if len(parts) == 2:
                source_part = parts[0].split("[", 1)
                if len(source_part) == 2:
                    source = source_part[1]
                summary = parts[1]
            parsed.append((source, summary.strip()))

        summaries = [s for _, s in parsed]
        analysis_results = await self._analyse_batch(summaries)

        results: list[tuple[EpisodicEntry, dict[str, Any]]] = []
        for (source, summary), analysis in zip(parsed, analysis_results, strict=True):
            entities: list[str] = analysis.get("entities", [])
            semantic_key: str = analysis.get("semantic_key", "")
            llm_significance: dict[str, Any] = analysis.get("significance", {})
            results.append(
                (
                    EpisodicEntry(
                        id=str(uuid4()),
                        timestamp=datetime.now(UTC),
                        source=source,
                        summary=summary,
                        entities=entities,
                        significance=SignificanceScore(overall=0.5),
                        semantic_key=semantic_key,
                        valence="neutral",
                    ),
                    llm_significance,
                )
            )
        return results

    @staticmethod
    def _write_semantic_file(path: Path, content: str) -> None:
        """Atomic write to a semantic memory file."""
        from shared.fs import atomic_write

        atomic_write(path, content)

    async def _resolve_conflicts(
        self,
        existing_content: str,
        new_summaries: str,
        conflict_min_observations: int,
        conflict_min_days: int,
    ) -> list[ConflictItem]:
        """Call Claude to compare new observations against existing learned.md.

        Returns a structured list of ConflictItem decisions.
        Falls back to empty list on any error.
        """
        try:
            import litellm  # runtime import — optional dependency

            system_prompt = (
                "You are a memory conflict resolver for a home automation assistant. "
                "Given EXISTING learned preferences and NEW observations, classify each new "
                "observation against the existing preferences.\n\n"
                "Return a JSON array where each element has:\n"
                '  "type": "confirm" | "contradict" | "new"\n'
                '  For "confirm": "line" (existing preference text), "explanation"\n'
                '  For "contradict": "old" (existing text), "new" (revised text), "explanation"\n'
                '  For "new": "content" (the new preference to add), "explanation"\n\n'
                f"IMPORTANT: Only allow 'contradict' if supported by at least "
                f"{conflict_min_observations} consistent observations over at least "
                f"{conflict_min_days} days. Otherwise use 'confirm' or 'new'.\n"
                "Return ONLY the JSON array.\n"
                "Example:\n"
                "[\n"
                '  {"type": "confirm", "line": "Prefers 72°F", '
                '"explanation": "Consistent with recent data"},\n'
                '  {"type": "contradict", "old": "Prefers 72°F", "new": "Prefers 68°F", '
                '"explanation": "Last 5 observations show 68°F"},\n'
                '  {"type": "new", "content": "Prefers warm lighting in evening", '
                '"explanation": "Observed 3 times this week"}\n'
                "]"
            )
            user_content = (
                f"EXISTING PREFERENCES:\n{existing_content}\n\nNEW OBSERVATIONS:\n{new_summaries}"
            )
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=800,
                api_key=self._api_key,
            )
            raw = response.choices[0].message.content or "[]"
            parsed = json.loads(raw)
            return [ConflictItem.model_validate(item) for item in parsed]
        except Exception as exc:
            logger.warning("Conflict resolution failed: %s", exc)
            return []

    def _apply_conflict_resolutions(
        self,
        existing_content: str,
        resolutions: list[ConflictItem],
        today: str,
    ) -> tuple[str, int]:
        """Apply conflict resolution decisions to existing content.

        Returns (updated_content, change_count).
        """
        changes = 0
        content = existing_content

        for item in resolutions:
            if item.type == "confirm":
                # No change needed — existing preference validated
                continue
            elif item.type == "contradict" and item.old and item.new:
                # Replace old line with revised line + provenance
                old_line = item.old.strip()
                new_line = (
                    f"- {item.new.strip()} "
                    f"[Revised on {today}: was '{old_line}' — {item.explanation}]"
                )
                # Try to find and replace the old line in the content
                for prefix in ("- ", ""):
                    candidate = f"{prefix}{old_line}"
                    if candidate in content:
                        content = content.replace(candidate, new_line, 1)
                        changes += 1
                        break
                else:
                    # Old line not found verbatim — append contradiction note
                    content = content.rstrip() + f"\n{new_line}\n"
                    changes += 1
            elif item.type == "new" and item.content:
                # Append new preference
                content = content.rstrip() + f"\n- {item.content.strip()}\n"
                changes += 1

        return content, changes

    async def _update_semantic_memory(
        self,
        entries: list[EpisodicEntry],
        conflict_min_observations: int = 5,
        conflict_min_days: int = 14,
    ) -> int:
        """Conflict-aware semantic memory update.

        Two-pass approach:
        1. Extract candidate preferences from new observations (same as before).
        2. Compare against existing learned.md: confirm / contradict / new.

        Returns the number of files updated.
        """
        if not self._api_key or not entries:
            return 0

        summaries = "\n".join(f"- {e.summary}" for e in entries)
        learned_path = self._preferences_dir / "learned.md"
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        try:
            import litellm  # runtime import — optional dependency

            # Pass 1: extract candidate preferences from observations
            extract_response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Analyze these home assistant observations. "
                            "If you detect a clear user preference (e.g., preferred temperature, "
                            "routine change, dietary preference), output it as:\n"
                            "PREFERENCE: <domain>: <observation>\n"
                            "Only output high-confidence observations. "
                            "If nothing is notable, output NONE."
                        ),
                    },
                    {"role": "user", "content": summaries},
                ],
                max_tokens=500,
                api_key=self._api_key,
            )
            result_text: str = extract_response.choices[0].message.content or ""
            if "NONE" in result_text or not result_text.strip():
                return 0

            candidate_lines = [
                line.replace("PREFERENCE:", "").strip()
                for line in result_text.splitlines()
                if line.startswith("PREFERENCE:")
            ]
            if not candidate_lines:
                return 0

            candidates_text = "\n".join(f"- {c}" for c in candidate_lines)

            # Read existing learned.md (or build skeleton)
            if learned_path.exists():
                existing_content = learned_path.read_text()
            else:
                existing_content = (
                    "---\ndomain: general\nupdated: "
                    f"{today}\n"
                    "confidence: librarian\n---\n\n# Learned Preferences\n\n"
                )

            # Pass 2: conflict resolution against existing content
            resolutions = await self._resolve_conflicts(
                existing_content=existing_content,
                new_summaries=candidates_text,
                conflict_min_observations=conflict_min_observations,
                conflict_min_days=conflict_min_days,
            )

            if not resolutions:
                # Fallback: append-only (same as old behaviour) when LLM fails
                updated = existing_content.rstrip() + "\n" + candidates_text + "\n"
                self._write_semantic_file(learned_path, updated)
                return 1

            updated_content, changes = self._apply_conflict_resolutions(
                existing_content, resolutions, today
            )

            if changes == 0:
                return 0

            self._write_semantic_file(learned_path, updated_content)
            return 1

        except Exception as exc:
            logger.warning("Semantic memory update failed: %s", exc)
        return 0

    async def _compress_and_migrate(self, group: list[SearchResult]) -> int:
        """Compress a group of related entries into a summary, then migrate originals.

        Calls LLM to generate a summary + semantic_key for the group. Falls back
        to concatenation if LLM fails or no API key. Writes summary to cold store
        directly (not via EpisodicMemory to avoid re-embedding), then migrates
        originals with compressed="yes" marker.

        Returns the number of original entries migrated.
        """
        from core.memory.vector_store import ContextMetadata

        lines = [f"- [{r.id}] {r.content}" for r in group]
        group_text = "\n".join(lines)

        # Aggregate metadata
        all_entities = sorted(
            {e.strip() for r in group for e in r.metadata.entities.split(",") if e.strip()}
        )
        max_significance = max(r.metadata.significance for r in group)
        min_timestamp = min(r.metadata.timestamp for r in group)
        total_retrievals = sum(r.metadata.retrieval_count for r in group)

        # Generate summary via LLM
        summary_text = ""
        semantic_key_text = ""
        if self._api_key:
            try:
                import litellm

                system_prompt = (
                    "You are a memory compressor for a home automation assistant. "
                    "Given multiple related episodic memory entries, produce a single "
                    "concise summary and a short semantic key phrase. "
                    "Return ONLY valid JSON with keys "
                    '"summary" and "semantic_key". No other text.\n'
                    'Example: {"summary": "Kitchen lights toggled on then off.", '
                    '"semantic_key": "kitchen light activity"}'
                )
                response = await litellm.acompletion(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": group_text},
                    ],
                    max_tokens=200,
                    api_key=self._api_key,
                )
                raw = response.choices[0].message.content or "{}"
                parsed: dict[str, str] = json.loads(raw)
                summary_text = parsed.get("summary", "")
                semantic_key_text = parsed.get("semantic_key", "")
            except Exception as exc:
                logger.warning("Compression: LLM summarization failed: %s", exc)

        # Fallback: concatenate contents
        if not summary_text:
            summary_text = " | ".join(r.content for r in group)
        if not semantic_key_text:
            semantic_key_text = summary_text[:100]

        # Write summary entry to cold store (direct add, no hot store)
        summary_id = str(uuid4())
        summary_metadata = ContextMetadata(
            type="episodic",
            source="librarian_compressed",
            entities=",".join(all_entities),
            timestamp=min_timestamp,
            significance=max_significance,
            retrieval_count=total_retrievals,
            last_retrieved=0.0,
            compressed="yes",
        )
        try:
            content_emb, key_emb = await asyncio.gather(
                self._embedder.embed(summary_text),
                self._embedder.embed(semantic_key_text),
            )
            await self._cold_store.add(
                id=summary_id,
                content=summary_text,
                semantic_key=semantic_key_text,
                embedding_content=content_emb,
                embedding_semantic=key_emb,
                metadata=summary_metadata,
            )
            logger.debug(
                "Compression: wrote summary %s for %d entries",
                summary_id,
                len(group),
            )
        except Exception as exc:
            logger.warning("Compression: failed to write summary entry: %s", exc)
            # Still migrate originals even if summary write fails

        # Migrate all originals with compressed marker
        migrated = 0
        for result in group:
            try:
                marked = result.model_copy(
                    update={"metadata": result.metadata.model_copy(update={"compressed": "yes"})}
                )
                await self._episodic_memory.copy_to_cold_and_remove(marked)
                migrated += 1
            except Exception as exc:
                logger.warning(
                    "Compression: failed to migrate original entry %s: %s", result.id, exc
                )

        return migrated

    async def _apply_decay(
        self,
        decay_migration_threshold: float = 1.0,
        search_query: str = "general context memory event",
        search_limit: int = 500,
    ) -> int:
        """Migrate old low-significance hot entries to cold storage.

        Uses a subtractive formula where significance and retrieval
        activity resist the migration pressure from age:

            age_factor = min(days_old / 30.0, 1.0)
            retrieval_recency = exp(-days_since_last_retrieved / 7.0)
            retrieval_frequency = min(log2(count + 1) / 5.0, 1.0)

            pressure = (
                age_factor
                - significance * 2.0
                - retrieval_recency * 1.5
                - retrieval_frequency * 1.0
            )

        Entries with pressure > decay_migration_threshold are migrated to cold.
        Related entries (same entity + same day) are compressed into a single
        summary before migration.
        Returns the number of entries migrated.
        """
        from math import exp, log2

        try:
            results = await self._context_index.search_text(
                query=search_query,
                limit=search_limit,
                min_similarity=0.0,
            )
        except Exception as exc:
            logger.warning("Decay: failed to retrieve hot entries: %s", exc)
            return 0

        now = datetime.now(UTC).timestamp()
        to_migrate: list[SearchResult] = []

        for result in results:
            if result.metadata.type != "episodic":
                continue

            timestamp = result.metadata.timestamp
            if timestamp <= 0:
                continue

            age_days = (now - timestamp) / 86400.0
            significance = result.metadata.significance
            retrieval_count = result.metadata.retrieval_count
            last_retrieved = result.metadata.last_retrieved

            # Fallback: if last_retrieved was never set, assume never retrieved
            if last_retrieved > 0:
                days_since_last_retrieved = (now - last_retrieved) / 86400.0
            else:
                days_since_last_retrieved = age_days

            age_factor = min(age_days / 30.0, 1.0)
            retrieval_recency = exp(-days_since_last_retrieved / 7.0)
            retrieval_frequency = min(log2(retrieval_count + 1) / 5.0, 1.0)

            pressure = (
                age_factor
                - significance * 2.0
                - retrieval_recency * 1.5
                - retrieval_frequency * 1.0
            )

            if pressure > decay_migration_threshold:
                to_migrate.append(result)
                logger.debug(
                    "Decay: queued entry %s (age=%.1fd, sig=%.2f, pressure=%.2f)",
                    result.id,
                    age_days,
                    significance,
                    pressure,
                )

        if not to_migrate:
            return 0

        # Group related entries for compression
        groups, ungrouped = _group_by_entity_date(to_migrate)

        # Compress groups and migrate ungrouped in parallel
        async def _migrate_single(result: SearchResult) -> int:
            try:
                await self._episodic_memory.copy_to_cold_and_remove(result)
                return 1
            except Exception as exc:
                logger.warning("Decay: failed to migrate entry %s: %s", result.id, exc)
                return 0

        async def _compress_group(group: list[SearchResult]) -> int:
            try:
                return await self._compress_and_migrate(group)
            except Exception as exc:
                logger.warning("Decay: compression failed for group: %s", exc)
                return 0

        migration_counts = await asyncio.gather(
            *(_compress_group(g) for g in groups),
            *(_migrate_single(r) for r in ungrouped),
        )
        migrated = sum(migration_counts)

        if migrated:
            logger.info("Decay: migrated %d entries to cold storage", migrated)
        return migrated

    async def _detect_patterns(
        self,
        recent_entries: list[EpisodicEntry],
    ) -> list[RoutineSpec]:
        """Detect repeated behavioural patterns across recent episodic entries.

        Calls Claude with the last 30 days of entries and asks it to identify
        patterns that occurred 3+ times over 7+ days.  Returns candidate
        ``RoutineSpec`` objects (state="candidate") that have not yet been saved;
        the caller decides whether to persist them.

        Falls back to an empty list when no API key is configured or on any
        error.
        """
        if not self._api_key or not recent_entries:
            return []

        # Gather all routines already stored to avoid re-creating duplicates
        existing_names: set[str] = {r.name for r in self._routines.list_all()}

        # Restrict to entries in the last 30 days
        cutoff = datetime.now(UTC) - timedelta(days=30)
        window_entries = [e for e in recent_entries if e.timestamp >= cutoff]
        if not window_entries:
            return []

        summaries = "\n".join(
            f"- [{e.id}] {e.timestamp.strftime('%Y-%m-%dT%H:%M')} {e.summary}"
            for e in window_entries
        )

        system_prompt = (
            "You are a pattern analyst for a home automation assistant. "
            "Given episodic memory entries, identify repeated behavioural patterns. "
            f"Only report patterns with at least {self._pattern_min_occurrences} occurrences "
            f"spread over at least {self._pattern_min_days} different days. "
            "\n\n"
            "PAY SPECIAL ATTENTION to entries with source 'reflex' — these are automatic "
            "System 1 (Reflex Engine) actions taken without conscious reasoning. Look for:\n"
            "- Repeated reflex actions that may be unnecessary or counterproductive "
            "(e.g., lights turning on for pet motion at night)\n"
            "- Reflex patterns that could be optimised or overridden by a learned routine\n"
            "- Reflex actions that are consistent and beneficial (validate as good behaviour)\n\n"
            "Return a JSON array of pattern objects. Each object must have:\n"
            '  "name": short snake_case identifier (e.g. "evening_dim")\n'
            '  "trigger_pattern": when it happens (e.g. "20:00 daily", "weekday morning")\n'
            '  "steps": array of {"description": str} objects\n'
            '  "confidence": float 0.0-1.0\n'
            '  "learned_from": array of episodic entry IDs (from the [id] prefix)\n\n'
            "Return ONLY the JSON array. If no patterns qualify, return [].\n"
            "Example:\n"
            '[\n  {"name": "evening_dim", "trigger_pattern": "20:00 daily",\n'
            '   "steps": [{"description": "Dim living room lights to 30%"}],\n'
            '   "confidence": 0.75, "learned_from": ["ep-1", "ep-3", "ep-7"]}\n]'
        )

        try:
            import litellm  # runtime import — optional dependency

            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summaries},
                ],
                max_tokens=1000,
                api_key=self._api_key,
            )
            raw: str = response.choices[0].message.content or "[]"
            parsed: list[dict[str, Any]] = json.loads(raw)
        except Exception as exc:
            logger.warning("Pattern detection LLM call failed: %s", exc)
            return []

        candidates: list[RoutineSpec] = []
        for item in parsed:
            try:
                name: str = item.get("name", "")
                if not name or name in existing_names:
                    continue
                confidence: float = float(item.get("confidence", 0.0))
                if confidence < self._pattern_confidence_threshold:
                    continue
                steps = [
                    RoutineStep(description=s.get("description", ""))
                    for s in item.get("steps", [])
                    if s.get("description")
                ]
                candidate = RoutineSpec(
                    name=name,
                    trigger_pattern=item.get("trigger_pattern", ""),
                    steps=steps,
                    confidence=confidence,
                    learned_from=item.get("learned_from", []),
                    state="candidate",
                )
                self._routines.save(candidate)
                candidates.append(candidate)
                logger.info(
                    "Pattern detected: '%s' (confidence=%.2f, occurrences=%d)",
                    name,
                    confidence,
                    len(candidate.learned_from),
                )
                # Index in context for involuntary recall
                try:
                    await self._context_index.index_routine(
                        id=candidate.name,
                        content=_routine_index_content(candidate),
                        confidence=candidate.confidence,
                    )
                except Exception as exc:
                    logger.warning("Failed to index routine '%s': %s", candidate.name, exc)
            except Exception as exc:
                logger.warning("Failed to process pattern candidate: %s", exc)

        return candidates

    async def _update_routine_lifecycle(self) -> int:
        """Apply lifecycle transitions to existing routines.

        For each active/candidate routine, checks whether its pattern
        fired recently (within the last consolidation window):
        - Pattern occurred → update ``last_hit``, reset ``consecutive_misses``
        - Pattern missed → increment ``consecutive_misses``
        - 3 consecutive misses → transition to ``dormant``
        - Dormant for 30 days → transition to ``archived``

        Returns the number of routines whose state was updated.
        """
        routines = self._routines.list_all()
        now = datetime.now(UTC)
        updated = 0

        for routine in routines:
            if routine.state == "archived":
                continue

            if routine.state == "dormant":
                # Check if dormant for 30+ days → archive
                if routine.last_hit is not None:
                    dormant_days = (now - routine.last_hit).days
                    if dormant_days >= 30:
                        routine = routine.model_copy(update={"state": "archived"})
                        self._routines.save(routine)
                        updated += 1
                        logger.info(
                            "Routine '%s' archived (dormant for %d days)",
                            routine.name,
                            dormant_days,
                        )
                        await self._remove_routine_from_index(routine.name)
                continue

            # For candidate/active: check if trigger_pattern matches recent activity
            pattern_fired = self._check_pattern_fired(routine, now)

            if pattern_fired:
                routine = routine.model_copy(
                    update={
                        "last_hit": now,
                        "consecutive_misses": 0,
                    }
                )
                self._routines.save(routine)
                updated += 1
                logger.debug("Routine '%s' hit", routine.name)
            else:
                new_misses = routine.consecutive_misses + 1
                new_state = routine.state
                new_confidence = routine.confidence

                # Confidence decay: if suggested but ignored (past cooldown, no acceptance)
                if (
                    routine.state == "candidate"
                    and routine.last_suggested is not None
                    and (now - routine.last_suggested).total_seconds() / 3600
                    >= self._routine_suggestion_cooldown_hours
                ):
                    new_confidence -= self._routine_decay_per_cycle

                if new_misses >= 3:
                    new_state = "dormant"
                    logger.info(
                        "Routine '%s' transitioned to dormant (%d consecutive misses)",
                        routine.name,
                        new_misses,
                    )

                # Archive if confidence drops below threshold
                if new_confidence < self._routine_archive_threshold:
                    new_state = "archived"
                    logger.info(
                        "Routine '%s' archived (confidence=%.2f below threshold %.2f)",
                        routine.name,
                        new_confidence,
                        self._routine_archive_threshold,
                    )

                routine = routine.model_copy(
                    update={
                        "consecutive_misses": new_misses,
                        "state": new_state,
                        "confidence": new_confidence,
                    }
                )
                self._routines.save(routine)
                updated += 1

                if new_state == "archived":
                    await self._remove_routine_from_index(routine.name)

        return updated

    async def _remove_routine_from_index(self, name: str) -> None:
        """Remove an archived routine from the context index."""
        try:
            await self._context_index.remove(name)
        except Exception as exc:
            logger.warning("Failed to remove archived routine '%s' from index: %s", name, exc)

    @staticmethod
    def _check_pattern_fired(routine: RoutineSpec, now: datetime) -> bool:
        """Heuristic check whether a routine's trigger_pattern matches the current time.

        Supports simple time patterns like "HH:MM daily" and "weekday morning".
        Returns ``True`` if the pattern likely fired in the past 24 hours.
        """
        from core.memory.routines.patterns import match_trigger_pattern

        return match_trigger_pattern(routine.trigger_pattern, now)

    async def _reindex_routines(self) -> int:
        """Re-index non-archived routines whose content has changed.

        Called at the start of each consolidation cycle to ensure routines
        loaded from YAML storage are searchable via involuntary recall.
        Skips routines whose indexed content hasn't changed (avoids re-embedding).
        """
        to_index: list[RoutineSpec] = []
        for routine in self._routines.list_all():
            if routine.state == "archived":
                continue
            content = _routine_index_content(routine)
            if self._indexed_routine_content.get(routine.name) == content:
                continue
            self._indexed_routine_content[routine.name] = content
            to_index.append(routine)

        if not to_index:
            return 0

        async def _index_one(r: RoutineSpec) -> bool:
            try:
                await self._context_index.index_routine(
                    id=r.name,
                    content=_routine_index_content(r),
                    confidence=r.confidence,
                )
                return True
            except Exception as exc:
                logger.warning("Failed to reindex routine '%s': %s", r.name, exc)
                return False

        results = await asyncio.gather(*(_index_one(r) for r in to_index))
        indexed = sum(results)
        if indexed:
            logger.info("Reindexed %d routines into context index", indexed)
        return indexed

    async def consolidate(self) -> dict[str, Any]:
        """Run one consolidation cycle.

        Returns a summary dict for logging/telemetry.
        """
        logger.info("Librarian consolidation started")

        # 0. Ensure routines from YAML store are indexed for involuntary recall
        routines_reindexed = await self._reindex_routines()

        # 1. Drain scratchpad
        lines = await self._drain_scratchpad()
        if not lines:
            logger.info("Scratchpad empty — nothing to consolidate")
            return {"entries_processed": 0, "routines_reindexed": routines_reindexed}

        logger.info("Draining %d scratchpad entries", len(lines))

        # 2. Extract episodic entries with LLM enrichment (entities + significance + semantic key)
        entry_pairs = await self._extract_episodic_entries(lines)

        # 3. Write to EpisodicMemory with merged heuristic + LLM significance
        for entry, llm_sig in entry_pairs:
            heuristic_score = await self._scorer.score(entry)
            if llm_sig:
                significance = SignificanceScore(
                    overall=heuristic_score.overall,
                    safety=max(heuristic_score.safety, llm_sig.get("safety", 0.0)),
                    novelty=max(heuristic_score.novelty, llm_sig.get("novelty", 0.0)),
                    personal=max(heuristic_score.personal, llm_sig.get("personal", 0.0)),
                    emotional=max(heuristic_score.emotional, llm_sig.get("emotional", 0.0)),
                    source="librarian",
                )
            else:
                significance = heuristic_score
            await self._episodic_memory.write(entry, significance)

        # 3b. All episodic writes succeeded — safe to delete the processing key
        await self._redis.delete(self._PROCESSING_KEY)

        episodic_entries = [entry for entry, _ in entry_pairs]

        # 4. Update semantic memory (requires Claude)
        semantic_updates = await self._update_semantic_memory(
            episodic_entries,
            conflict_min_observations=self._conflict_min_observations,
            conflict_min_days=self._conflict_min_days,
        )

        # 5. Pattern detection for procedural memory
        patterns_detected = await self._detect_patterns(episodic_entries)

        # 6. Routine lifecycle updates
        lifecycle_updates = await self._update_routine_lifecycle()

        # 7. Decay processing
        archived = await self._apply_decay(
            decay_migration_threshold=self._decay_migration_threshold,
        )

        # 8. Re-index semantic files so context search reflects latest learned.md etc.
        await self._context_index.reindex_semantic_files()

        result: dict[str, Any] = {
            "entries_processed": len(lines),
            "episodic_created": len(episodic_entries),
            "semantic_updates": semantic_updates,
            "patterns_detected": len(patterns_detected),
            "routines_reindexed": routines_reindexed,
            "lifecycle_updates": lifecycle_updates,
            "archived": archived,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.info("Consolidation complete: %s", result)
        return result
