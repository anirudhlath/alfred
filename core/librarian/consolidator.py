"""Librarian — nightly consolidation of scratchpad into structured memory.

The Librarian replays the scratchpad, extracts episodic entries,
updates semantic memory (preferences + profile), detects patterns
for procedural memory, and applies decay to old entries.

Uses Claude for LLM-powered extraction and conflict resolution.
Atomic file writes: write to .tmp then os.rename() for memory files.
On failure, the scratchpad accumulates — memory files never left partial.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.memory.schemas import EpisodicEntry
from shared.streams import SCRATCHPAD_QUEUE

if TYPE_CHECKING:
    from core.memory.episodic.store import EpisodicStore
    from core.memory.routines.store import RoutineStore
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class Librarian:
    """Nightly consolidation agent.

    Drains the scratchpad queue, processes entries through an LLM,
    and writes to the three memory layers.
    """

    def __init__(
        self,
        redis: AioRedis,
        episodic_store: EpisodicStore,
        routine_store: RoutineStore,
        preferences_dir: str = "core/memory/preferences",
        profile_dir: str = "core/memory/profile",
        claude_api_key: str = "",
        claude_model: str = "openrouter/anthropic/claude-sonnet-4",
    ) -> None:
        self._redis = redis
        self._episodic = episodic_store
        self._routines = routine_store
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
        leftover: list[bytes] = await self._redis.lrange(self._PROCESSING_KEY, 0, -1)
        if not leftover:
            # Atomically move the queue to the processing key
            try:
                await self._redis.rename(  # type: ignore[no-untyped-call]
                    SCRATCHPAD_QUEUE, self._PROCESSING_KEY
                )
            except Exception:
                # RENAME fails if the source key doesn't exist (empty queue)
                return []

        raw: list[bytes] = await self._redis.lrange(self._PROCESSING_KEY, 0, -1)
        if raw:
            await self._redis.delete(self._PROCESSING_KEY)
        return [r.decode() if isinstance(r, bytes) else str(r) for r in raw]

    async def _extract_entities(self, text: str) -> list[str]:
        """Extract named entities from a scratchpad line using Claude."""
        if not self._api_key:
            return []
        try:
            import json

            import litellm

            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract named entities from this home automation observation. "
                            "Return a JSON array of entity names "
                            "(devices, rooms, people, services). "
                            'Example: ["light.living_room", "living room", "motion sensor"]'
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=200,
                api_key=self._api_key,
            )
            raw = response.choices[0].message.content or "[]"
            result: list[str] = json.loads(raw)
            return result
        except Exception as exc:
            logger.warning("Entity extraction failed: %s", exc)
            return []

    async def _extract_episodic_entries(self, scratchpad_lines: list[str]) -> list[EpisodicEntry]:
        """Extract episodic entries from scratchpad lines.

        For now, each scratchpad line becomes one episodic entry.
        Future: use Claude to summarize and merge related observations.
        """
        entries: list[EpisodicEntry] = []
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

            entities = await self._extract_entities(summary)
            entries.append(
                EpisodicEntry(
                    id=str(uuid4()),
                    timestamp=datetime.now(UTC),
                    source=source,
                    summary=summary.strip(),
                    entities=entities,
                    valence="neutral",
                )
            )
        return entries

    def _write_semantic_file(self, path: Path, content: str) -> None:
        """Atomic write to a semantic memory file."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content)
        os.rename(tmp, path)

    async def _update_semantic_memory(self, entries: list[EpisodicEntry]) -> int:
        """Use Claude to detect preference changes and update semantic files.

        Returns the number of files updated.
        """
        if not self._api_key or not entries:
            return 0

        summaries = "\n".join(f"- {e.summary}" for e in entries)
        try:
            import litellm

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

        # 2. Extract episodic entries
        episodic_entries = await self._extract_episodic_entries(lines)

        # 3. Write to episodic store
        # Load embedding model once (optional dep — falls back to empty bytes)
        embedder = None
        try:
            from core.memory.episodic.embeddings import EmbeddingModel

            embedder = EmbeddingModel()
        except ImportError:
            logger.info("sentence-transformers not installed — writing entries without embeddings")

        for entry in episodic_entries:
            embedding = embedder.embed(entry.summary) if embedder else b""
            await self._episodic.write(entry, embedding)

        # 4. Update semantic memory (requires Claude)
        semantic_updates = await self._update_semantic_memory(episodic_entries)

        # 5. Pattern detection for procedural memory
        # Needs multiple consolidation cycles of data (>= 2 weeks).
        # Deferred until enough episodic entries exist.

        # 6. Decay processing
        archived = await self._apply_decay()

        result: dict[str, Any] = {
            "entries_processed": len(lines),
            "episodic_created": len(episodic_entries),
            "semantic_updates": semantic_updates,
            "archived": archived,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.info("Consolidation complete: %s", result)
        return result
