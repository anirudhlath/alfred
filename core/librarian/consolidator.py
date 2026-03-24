"""Librarian — nightly consolidation of scratchpad into structured memory.

The Librarian replays the scratchpad, extracts episodic entries,
updates semantic memory (preferences + profile), detects patterns
for procedural memory, and applies decay to old entries.

Uses Claude for LLM-powered extraction and conflict resolution.
Atomic file writes: write to .tmp then os.rename() for memory files.
On failure, the scratchpad accumulates — memory files never left partial.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.memory.schemas import EpisodicEntry, SignificanceScore
from shared.streams import SCRATCHPAD_QUEUE

if TYPE_CHECKING:
    from core.memory.context_index import ContextIndexManager
    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.routines.store import RoutineStore
    from core.memory.significance import SignificanceScorer
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
_DEFAULT_PREFERENCES_DIR = str(_MEMORY_DIR / "preferences")
_DEFAULT_PROFILE_DIR = str(_MEMORY_DIR / "profile")


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
    ) -> None:
        self._redis = redis
        self._episodic_memory = episodic_memory
        self._routines = routine_store
        self._scorer = significance_scorer
        self._context_index = context_index
        self._preferences_dir = Path(preferences_dir)
        self._profile_dir = Path(profile_dir)
        self._api_key = claude_api_key
        self._model = claude_model

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

    async def _update_semantic_memory(self, entries: list[EpisodicEntry]) -> int:
        """Use Claude to detect preference changes and update semantic files.

        Returns the number of files updated.
        """
        if not self._api_key or not entries:
            return 0

        summaries = "\n".join(f"- {e.summary}" for e in entries)
        try:
            import litellm  # runtime import — optional dependency

            response = await litellm.acompletion(
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
            result_text: str = response.choices[0].message.content or ""
            if "NONE" in result_text or not result_text.strip():
                return 0

            new_lines = [
                line.replace("PREFERENCE:", "-").strip()
                for line in result_text.splitlines()
                if line.startswith("PREFERENCE:")
            ]
            if not new_lines:
                return 0

            learned_path = self._preferences_dir / "learned.md"
            if learned_path.exists():
                existing = learned_path.read_text()
            else:
                existing = (
                    "---\ndomain: general\nupdated: "
                    f"{datetime.now(UTC).strftime('%Y-%m-%d')}\n"
                    "confidence: librarian\n---\n\n# Learned Preferences\n\n"
                )

            updated = existing.rstrip() + "\n" + "\n".join(new_lines) + "\n"
            self._write_semantic_file(learned_path, updated)
            return 1
        except Exception as exc:
            logger.warning("Semantic memory update failed: %s", exc)
        return 0

    async def _apply_decay(self) -> int:
        """Archive old hot-storage entries to cold storage.

        Returns the number of entries archived.
        The EpisodicStore hot→cold migration is handled by the store itself.
        This is a placeholder for future time-based XTRIM or archival.
        """
        return 0

    async def consolidate(self) -> dict[str, Any]:
        """Run one consolidation cycle.

        Returns a summary dict for logging/telemetry.
        """
        logger.info("Librarian consolidation started")

        # 1. Drain scratchpad
        lines = await self._drain_scratchpad()
        if not lines:
            logger.info("Scratchpad empty — nothing to consolidate")
            return {"entries_processed": 0}

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
        semantic_updates = await self._update_semantic_memory(episodic_entries)

        # 5. Pattern detection for procedural memory
        # Needs multiple consolidation cycles of data (>= 2 weeks).
        # Deferred until enough episodic entries exist.

        # 6. Decay processing
        archived = await self._apply_decay()

        # 7. Re-index semantic files so context search reflects latest learned.md etc.
        await self._context_index.reindex_semantic_files()

        result: dict[str, Any] = {
            "entries_processed": len(lines),
            "episodic_created": len(episodic_entries),
            "semantic_updates": semantic_updates,
            "archived": archived,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.info("Consolidation complete: %s", result)
        return result
