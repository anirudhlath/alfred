"""Memory Ingestor — the hippocampus.

Consumes ReflexObservation events from the observation stream and
writes them directly to episodic memory. This is the bridge between
System 1 actions and System 2 awareness.

Runs as a background task in the unified runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from loguru import logger

from bus.schemas.events import ReflexObservation
from core.memory.schemas import EpisodicEntry, SignificanceScore
from shared.streams import REFLEX_OBSERVATIONS_STREAM, decode_stream_value

if TYPE_CHECKING:
    import asyncio

    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.significance import SignificanceScorer
    from shared.types import AioRedis

GROUP = "memory-ingestor"
CONSUMER = "worker-1"


def _build_summary(obs: ReflexObservation) -> str:
    """Build a human-readable summary for embedding."""
    params_str = ", ".join(f"{k}={v}" for k, v in obs.action.parameters.items())
    base = f"[reflex:{obs.origin}] {obs.action.tool_name}({params_str}) → {obs.result.status}"
    if obs.decision_context:
        base += f" | reason: {obs.decision_context}"
    return base


def _build_semantic_key(obs: ReflexObservation) -> str:
    """Build a semantic key optimised for vector search."""
    param_vals = (
        [str(v) for v in obs.action.parameters.values()] if obs.action.parameters else ["unknown"]
    )
    return f"Reflex {obs.origin} action: {obs.action.tool_name} on {', '.join(param_vals)}"


def _extract_entities(obs: ReflexObservation) -> list[str]:
    """Extract entity IDs from the observation."""
    entities: set[str] = set()
    # From action parameters (entity_id is common)
    for key in ("entity_id", "room", "device"):
        val = obs.action.parameters.get(key)
        if val and isinstance(val, str):
            entities.add(val)
    # From trigger_event
    val = obs.trigger_event.get("entity_id")
    if val and isinstance(val, str):
        entities.add(val)
    return sorted(entities)


async def ingest_observation(
    obs: ReflexObservation,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
) -> None:
    """Convert a ReflexObservation into an episodic entry and store it."""
    entry = EpisodicEntry(
        id=str(uuid4()),
        timestamp=obs.timestamp,
        source="reflex",
        summary=_build_summary(obs),
        entities=_extract_entities(obs),
        significance=SignificanceScore(overall=0.0),  # placeholder, scored below
        semantic_key=_build_semantic_key(obs),
        valence="neutral",
    )

    significance = await scorer.score(entry)
    await episodic_memory.write(entry, significance)
    logger.debug("Ingested reflex observation {}: {}", obs.observation_id, entry.summary)


async def run_ingestor(
    redis: AioRedis,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Consumer loop — reads REFLEX_OBSERVATIONS_STREAM, writes to episodic memory."""
    from core.reflex.runner import ensure_consumer_group

    await ensure_consumer_group(redis, REFLEX_OBSERVATIONS_STREAM, GROUP)
    logger.info("Memory Ingestor started. Consuming '{}'...", REFLEX_OBSERVATIONS_STREAM)

    while True:
        if shutdown_event and shutdown_event.is_set():
            break

        entries: list[
            tuple[
                bytes | str,
                list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
            ]
        ] = await redis.xreadgroup(
            GROUP,
            CONSUMER,
            {REFLEX_OBSERVATIONS_STREAM: ">"},
            count=10,
            block=5000,
        )

        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                try:
                    raw = entry_data.get("event") or entry_data.get(b"event")
                    if raw is None:
                        await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
                        continue

                    event_str = decode_stream_value(raw)
                    obs = ReflexObservation.model_validate_json(event_str)
                    await ingest_observation(obs, episodic_memory, scorer)
                    await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
                except Exception as e:
                    logger.error(
                        "Error ingesting observation {} — will retry: {}",
                        entry_id,
                        e,
                    )
