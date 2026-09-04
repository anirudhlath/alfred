"""Memory Ingestor — the hippocampus.

Consumes ReflexObservation events from the observation stream and
writes them directly to episodic memory. This is the bridge between
System 1 actions and System 2 awareness.

Runs as a background task in the unified runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from bus.schemas.events import ReflexObservation
from core.memory.schemas import EpisodicEntry, SignificanceScore
from shared.redis_streams import read_group, reclaim_stale
from shared.streams import (
    INGEST_ATTEMPTS_KEY,
    REFLEX_OBSERVATIONS_STREAM,
    decode_stream_value,
)

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
# XAUTOCLAIM budget per reclaim pass. reclaim_stale always rescans from the head
# of the PEL (start_id="0-0") and discards the cursor, so this is a *window on
# the head*, not a page — see _MAX_DELIVERY_ATTEMPTS.
_PEL_RECLAIM_COUNT = 10
# How many times one stream entry may be delivered before it is ACKed away.
#
# Two problems, one cap. (1) SignificanceScorer._score_novelty ZINCRBYs before
# EpisodicMemory.write, so every reclaim of an entry that dies on the embed path
# counts its entities again and permanently deflates novelty (1/count) once the
# outage clears. (2) A parseable entry that fails deterministically sits at the
# head of the PEL and eats the whole _PEL_RECLAIM_COUNT budget every pass, so
# nothing behind it is ever reclaimed. Bounding deliveries fixes both.
#
# Five is ~5 minutes of transient tolerance at the one-reclaim-per-minute
# cadence, which covers a GPU-OOM blip on the embed path. Past that the entry is
# ACKed and logged at ERROR: a passive observation is cheap to lose (~250/day of
# a highly redundant signal) and a wedged PEL is not.
_MAX_DELIVERY_ATTEMPTS = 5
# Counters are deleted on success and on drop; the whole-key TTL only bounds
# what a crash between increment and cleanup can leak.
_ATTEMPTS_TTL_SECONDS = 3600

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
    """Summarise a state change nobody acted on.

    Salient attributes are rendered ``key=value``. A bare ``178`` or ``0`` is
    uninterpretable to the consolidation LLM, which sees only ``- {summary}``,
    and is close to noise in the embedding.
    """
    entity, old_state, new_state = _transition(obs)
    attributes = obs.trigger_event.get("attributes") or {}
    salient = [
        f"{key}={attributes[key]}"
        for key in SALIENT_ATTRIBUTES
        if attributes.get(key) not in (None, "")
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
    passive_scorer: SignificanceScorer,
) -> None:
    """Convert a ReflexObservation into an episodic entry and store it.

    ``obs.action is None`` means the Reflex Engine saw the event and took no
    action. Those are stored under source ``"observation"`` and scored by
    ``passive_scorer`` (which tracks its own entity-frequency population, so
    passive volume cannot flatten novelty for real reflex actions).

    ``passive_scorer`` is REQUIRED, deliberately: it had a ``None`` default for
    one commit, the sole production caller omitted it, and every passive
    observation was silently scored against the shared ``ENTITY_FREQUENCY_KEY``
    — the exact contamination this split exists to prevent, with nothing raised
    and nothing logged. A required argument makes that omission a type error.
    """
    # `action`/`result` are bound to locals inside the branch so mypy narrows
    # them for the summary builders. An action without a result is unreachable
    # in practice (publish_observation always sets both) — treating it as
    # passive degrades gracefully rather than crashing the ingest loop.
    action, result = obs.action, obs.result
    if action is None or result is None:
        active_scorer, source = passive_scorer, "observation"
        summary = _build_observation_summary(obs)
        semantic_key = _build_observation_semantic_key(obs)
    else:
        active_scorer, source = scorer, "reflex"
        summary = _build_summary(obs, action, result)
        semantic_key = _build_semantic_key(obs, action)

    entry = EpisodicEntry(
        # The observation id, NOT a fresh uuid: it is minted at publish time and
        # serialized into the stream, so it survives redelivery. RedisVectorStore
        # HSETs at f"{CONTEXT_PREFIX}{id}", which makes a reclaim-driven retry an
        # idempotent overwrite instead of a second copy of the same event.
        id=obs.observation_id,
        timestamp=obs.timestamp,
        source=source,
        summary=summary,
        entities=_extract_entities(obs),
        significance=SignificanceScore(overall=0.0),  # placeholder, scored below
        semantic_key=semantic_key,
        valence="neutral",
    )

    significance = await active_scorer.score(entry)
    await episodic_memory.write(entry, significance)
    logger.debug("Ingested observation {}: {}", obs.observation_id, entry.summary)


async def _record_failed_attempt(redis: AioRedis, entry_id: bytes | str) -> int:
    """Count this delivery of ``entry_id`` and return the running total.

    Bookkeeping is best-effort: if the hash write fails we report 1, which
    degrades to the old unbounded-retry behaviour rather than dropping an entry
    because Redis hiccuped.
    """
    field = decode_stream_value(entry_id)
    try:
        attempts = int(await redis.hincrby(INGEST_ATTEMPTS_KEY, field, 1))
        await redis.expire(INGEST_ATTEMPTS_KEY, _ATTEMPTS_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Could not record a delivery attempt for {}: {}", entry_id, exc)
        return 1
    return attempts


async def _clear_attempts(redis: AioRedis, entry_id: bytes | str) -> None:
    """Forget an entry's delivery history once it has left the PEL."""
    try:
        await redis.hdel(INGEST_ATTEMPTS_KEY, decode_stream_value(entry_id))
    except Exception as exc:
        logger.warning("Could not clear the delivery counter for {}: {}", entry_id, exc)


async def _ingest_entry(
    redis: AioRedis,
    entry_id: bytes | str,
    entry_data: Mapping[bytes | str, bytes | str],
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    passive_scorer: SignificanceScorer,
) -> None:
    """Ingest one stream entry, deciding whether it may stay pending.

    Two failure classes, two dispositions:

    * **Permanent** (no ``event`` field, or a payload that cannot be parsed) —
      ACKed and dropped. Retrying cannot change the outcome, and ``reclaim_stale``
      never ACKs, so a poison entry left pending would be reclaimed for eternity.
    * **Transient** (the embed/write path: GPU OOM, Redis blip) — left un-ACKed
      so the next reclaim pass picks it back up. This is the case the loop
      previously lost outright.

    The third case is an entry that *parses* but fails deterministically
    downstream: indistinguishable from transient at the failure site, so it is
    retried — but only ``_MAX_DELIVERY_ATTEMPTS`` times, after which it is ACKed
    and logged at ERROR. Otherwise it is reclaimed forever, re-incrementing the
    observed-frequency counters for its entities on every pass and holding the
    head of the PEL against everything behind it.
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
        await ingest_observation(obs, episodic_memory, scorer, passive_scorer)
    except Exception as e:
        attempts = await _record_failed_attempt(redis, entry_id)
        if attempts >= _MAX_DELIVERY_ATTEMPTS:
            logger.error(
                "Observation {} failed {} deliveries — giving up and dropping it: {}",
                entry_id,
                attempts,
                e,
            )
            await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
            await _clear_attempts(redis, entry_id)
            return
        # Deliberately NOT ACKed — the reclaim pass retries it.
        logger.error(
            "Error ingesting observation {} (attempt {}/{}) — left pending for reclaim: {}",
            entry_id,
            attempts,
            _MAX_DELIVERY_ATTEMPTS,
            e,
        )
        return

    await redis.xack(REFLEX_OBSERVATIONS_STREAM, GROUP, entry_id)
    await _clear_attempts(redis, entry_id)


async def run_ingestor(
    redis: AioRedis,
    episodic_memory: EpisodicMemory,
    scorer: SignificanceScorer,
    passive_scorer: SignificanceScorer,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Consumer loop — reads REFLEX_OBSERVATIONS_STREAM, writes to episodic memory."""
    from core.reflex.runner import ensure_consumer_group

    await ensure_consumer_group(redis, REFLEX_OBSERVATIONS_STREAM, GROUP)
    logger.info("Memory Ingestor started. Consuming '{}'...", REFLEX_OBSERVATIONS_STREAM)

    pel_counter = 0
    full_batch_streak = 0
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
            reclaimed = await reclaim_stale(
                redis,
                REFLEX_OBSERVATIONS_STREAM,
                GROUP,
                CONSUMER,
                count=_PEL_RECLAIM_COUNT,
            )
            if reclaimed:
                logger.info(
                    "Reclaimed {} pending observations on '{}'",
                    len(reclaimed),
                    REFLEX_OBSERVATIONS_STREAM,
                )
            # reclaim_stale rescans from the head of the PEL every pass, so a
            # full batch two passes running means the head is not draining and
            # whatever sits behind it is never being reached.
            if len(reclaimed) >= _PEL_RECLAIM_COUNT:
                full_batch_streak += 1
            else:
                full_batch_streak = 0
            if full_batch_streak >= 2:
                logger.warning(
                    "A full reclaim batch ({}) came back {} passes running on '{}' — "
                    "the head of the PEL is not draining and entries behind it are starving",
                    _PEL_RECLAIM_COUNT,
                    full_batch_streak,
                    REFLEX_OBSERVATIONS_STREAM,
                )
            batch.extend(reclaimed)

        for entry_id, entry_data in batch:
            await _ingest_entry(
                redis, entry_id, entry_data, episodic_memory, scorer, passive_scorer
            )
