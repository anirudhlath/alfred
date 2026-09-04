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
from shared.redis_streams import read_group, reclaim_stale
from shared.streams import REFLEX_OBSERVATIONS_STREAM, decode_stream_value

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Mapping

    from bus.schemas.events import ActionRequest, ActionResult
    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.significance import SignificanceScorer
    from shared.types import AioRedis

GROUP = "memory-ingestor"
CONSUMER = "worker-1"
# One PEL recovery pass per minute at the loop's 5s block.
_PEL_RECLAIM_EVERY = 12

# Folded into passive observation summaries so the consolidation LLM has
# something to correlate on beyond the bare state transition.
SALIENT_ATTRIBUTES = ("media_title", "brightness", "temperature", "friendly_name")


def _build_summary(obs: ReflexObservation, action: ActionRequest, result: ActionResult) -> str:
    """Build a human-readable summary for embedding."""
    params_str = ", ".join(f"{k}={v}" for k, v in action.parameters.items())
    base = f"[reflex:{obs.origin}] {action.tool_name}({params_str}) → {result.status}"
    if obs.decision_context:
        base += f" | reason: {obs.decision_context}"
    return base


def _build_semantic_key(obs: ReflexObservation, action: ActionRequest) -> str:
    """Build a semantic key optimised for vector search."""
    param_vals = [str(v) for v in action.parameters.values()] if action.parameters else ["unknown"]
    return f"Reflex {obs.origin} action: {action.tool_name} on {', '.join(param_vals)}"


def _extract_entities(obs: ReflexObservation) -> list[str]:
    """Extract entity IDs from the observation."""
    entities: set[str] = set()
    # From action parameters (entity_id is common). Absent on passive
    # observations, which carry only the trigger event.
    if obs.action is not None:
        for key in ("entity_id", "room", "device"):
            val = obs.action.parameters.get(key)
            if val and isinstance(val, str):
                entities.add(val)
    # From trigger_event
    val = obs.trigger_event.get("entity_id")
    if val and isinstance(val, str):
        entities.add(val)
    return sorted(entities)


def _transition(obs: ReflexObservation) -> tuple[str, str, str]:
    """Entity, old state, new state — from the raw trigger_event dict."""
    event = obs.trigger_event
    entity = str(event.get("entity_id") or "unknown")
    old_state = str(event.get("old_state") or "unknown")
    new_state = str(event.get("new_state") or "unknown")
    return entity, old_state, new_state


def _build_observation_summary(obs: ReflexObservation) -> str:
    """Summarise a state change nobody acted on."""
    entity, old_state, new_state = _transition(obs)
    attributes = obs.trigger_event.get("attributes") or {}
    salient = [
        str(attributes[key]) for key in SALIENT_ATTRIBUTES if attributes.get(key) not in (None, "")
    ]
    suffix = f" ({', '.join(salient)})" if salient else ""
    return f"[observation] {entity}: {old_state} → {new_state}{suffix}"


def _build_observation_semantic_key(obs: ReflexObservation) -> str:
    entity, old_state, new_state = _transition(obs)
    return f"Observed {entity} change from {old_state} to {new_state}"


async def ingest_observation(
    obs: ReflexObservation,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    passive_scorer: SignificanceScorer | None = None,
) -> None:
    """Convert a ReflexObservation into an episodic entry and store it.

    ``obs.action is None`` means the Reflex Engine saw the event and took no
    action. Those are stored under source ``"observation"`` and scored by
    ``passive_scorer`` (which tracks its own entity-frequency population, so
    passive volume cannot flatten novelty for real reflex actions).
    """
    # Bound to locals so mypy narrows them for the action branch below.
    # An action without a result is unreachable in practice (publish_observation
    # always sets both) — treating it as passive degrades gracefully rather
    # than crashing the ingest loop.
    action, result = obs.action, obs.result
    passive = action is None or result is None

    if action is None or result is None:
        entry = EpisodicEntry(
            id=str(uuid4()),
            timestamp=obs.timestamp,
            source="observation",
            summary=_build_observation_summary(obs),
            entities=_extract_entities(obs),
            significance=SignificanceScore(overall=0.0),  # placeholder, scored below
            semantic_key=_build_observation_semantic_key(obs),
            valence="neutral",
        )
    else:
        entry = EpisodicEntry(
            id=str(uuid4()),
            timestamp=obs.timestamp,
            source="reflex",
            summary=_build_summary(obs, action, result),
            entities=_extract_entities(obs),
            significance=SignificanceScore(overall=0.0),  # placeholder, scored below
            semantic_key=_build_semantic_key(obs, action),
            valence="neutral",
        )

    active_scorer = passive_scorer if (passive and passive_scorer is not None) else scorer
    significance = await active_scorer.score(entry)
    await episodic_memory.write(entry, significance)
    logger.debug("Ingested observation {}: {}", obs.observation_id, entry.summary)


async def _ingest_entry(
    redis: AioRedis,
    entry_id: bytes | str,
    entry_data: Mapping[bytes | str, bytes | str],
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    passive_scorer: SignificanceScorer | None,
) -> None:
    """Ingest one stream entry, deciding whether it may stay pending.

    Two failure classes, two dispositions:

    * **Permanent** (no ``event`` field, or a payload that cannot be parsed) —
      ACKed and dropped. Retrying cannot change the outcome, and ``reclaim_stale``
      never ACKs, so a poison entry left pending would be reclaimed for eternity.
    * **Transient** (the embed/write path: GPU OOM, Redis blip) — left un-ACKed
      so the next reclaim pass picks it back up. This is the case the loop
      previously lost outright.
    """
    raw = entry_data.get("event") or entry_data.get(b"event")
    if raw is None:
        logger.warning("Observation {} has no 'event' field — discarding", entry_id)
        await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
        return

    try:
        obs = ReflexObservation.model_validate_json(decode_stream_value(raw))
    except Exception as e:
        logger.error("Observation {} is unparseable — discarding: {}", entry_id, e)
        await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
        return

    try:
        await ingest_observation(obs, episodic_memory, scorer, passive_scorer=passive_scorer)
    except Exception as e:
        # Deliberately NOT ACKed — the reclaim pass retries it.
        logger.error("Error ingesting observation {} — left pending for reclaim: {}", entry_id, e)
        return

    await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)


async def run_ingestor(
    redis: AioRedis,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    shutdown_event: asyncio.Event | None = None,
    passive_scorer: SignificanceScorer | None = None,
) -> None:
    """Consumer loop — reads REFLEX_OBSERVATIONS_STREAM, writes to episodic memory."""
    from core.reflex.runner import ensure_consumer_group

    await ensure_consumer_group(redis, REFLEX_OBSERVATIONS_STREAM, GROUP)
    logger.info("Memory Ingestor started. Consuming '{}'...", REFLEX_OBSERVATIONS_STREAM)

    pel_counter = 0
    while not (shutdown_event and shutdown_event.is_set()):
        entries = await read_group(
            redis,
            GROUP,
            CONSUMER,
            {REFLEX_OBSERVATIONS_STREAM: ">"},
            count=10,
            block=5000,
        )
        batch = [pair for _stream_key, stream_entries in entries for pair in stream_entries]

        # XREADGROUP '>' only ever delivers NEW messages, so an entry left
        # un-ACKed by a failed ingest is never redelivered on its own — without
        # this it is lost and its PEL slot leaks. reclaim_stale, NOT
        # reclaim_replayable: the 5-minute replay window means "too stale to act
        # on", and a ten-minute-old observation is still worth remembering.
        pel_counter += 1
        if pel_counter >= _PEL_RECLAIM_EVERY:
            pel_counter = 0
            reclaimed = await reclaim_stale(redis, REFLEX_OBSERVATIONS_STREAM, GROUP, CONSUMER)
            if reclaimed:
                logger.info(
                    "Reclaimed {} pending observations on '{}'",
                    len(reclaimed),
                    REFLEX_OBSERVATIONS_STREAM,
                )
            batch.extend(reclaimed)

        for entry_id, entry_data in batch:
            await _ingest_entry(
                redis, entry_id, entry_data, episodic_memory, scorer, passive_scorer
            )
