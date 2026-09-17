"""Ingesting observations that record a non-action."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ReflexObservation

if TYPE_CHECKING:
    from core.memory.schemas import EpisodicEntry


def _passive(
    entity_id: str = "media_player.living_room_apple_tv",
    old_state: str = "paused",
    new_state: str = "playing",
    attributes: object = None,
) -> ReflexObservation:
    return ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={
            "entity_id": entity_id,
            "old_state": old_state,
            "new_state": new_state,
            "attributes": attributes if attributes is not None else {},
        },
    )


def _mock_scorer(overall: float = 0.2) -> AsyncMock:
    mock = AsyncMock()
    mock.score = AsyncMock(
        return_value=MagicMock(
            overall=overall, safety=0.0, novelty=0.3, personal=0.3, emotional=0.2
        )
    )
    return mock


@pytest.fixture
def scorer() -> AsyncMock:
    return _mock_scorer()


@pytest.fixture
def passive_scorer() -> AsyncMock:
    return _mock_scorer(overall=0.1)


@pytest.mark.asyncio
async def test_passive_observation_uses_the_observation_source(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive(), episodic, scorer, passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.source == "observation"


@pytest.mark.asyncio
async def test_summary_describes_the_transition(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive(attributes={"media_title": "Harry Potter"}), episodic, scorer, passive_scorer
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == (
        "[observation] media_player.living_room_apple_tv: "
        "paused → playing (media_title=Harry Potter)"
    )


@pytest.mark.asyncio
async def test_summary_without_salient_attributes(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive("binary_sensor.front_door", "off", "on"), episodic, scorer, passive_scorer
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] binary_sensor.front_door: off → on"


@pytest.mark.asyncio
async def test_salient_attributes_are_folded_in_declared_order(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """Rendered ``key=value``: a bare ``178`` is uninterpretable to the consolidation LLM."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive(
            "light.kitchen",
            "off",
            "on",
            attributes={
                "friendly_name": "Kitchen Light",
                "brightness": 178,
                "unrelated": "ignored",
            },
        ),
        episodic,
        scorer,
        passive_scorer,
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == (
        "[observation] light.kitchen: off → on (brightness=178, friendly_name=Kitchen Light)"
    )
    assert "ignored" not in entry.summary


@pytest.mark.asyncio
async def test_a_zero_valued_attribute_is_still_rendered(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """``brightness=0`` is a real reading — the filter drops None and "" only."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive("light.kitchen", "on", "off", attributes={"brightness": 0}),
        episodic,
        scorer,
        passive_scorer,
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] light.kitchen: on → off (brightness=0)"


@pytest.mark.asyncio
async def test_non_mapping_attributes_do_not_crash_the_ingest(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """``trigger_event`` is a raw dict off the wire — ``attributes`` may be anything.

    Its neighbours already guard their shapes (``_extract_entities`` checks
    ``isinstance(val, str)``, ``_transition`` coerces with ``str()``); a list
    here raised ``AttributeError: 'list' object has no attribute 'get'``.
    """
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive("light.kitchen", "off", "on", attributes=["not", "a", "dict"]),
        episodic,
        scorer,
        passive_scorer,
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] light.kitchen: off → on"


@pytest.mark.asyncio
async def test_a_falsy_state_is_not_rewritten_as_unknown(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """``0`` and ``""`` are states, not missing states — only ``None`` is unknown.

    The attribute filter two functions away deliberately keeps a ``0``; the
    transition renderer's ``or "unknown"`` did not.
    """
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "sensor.power", "old_state": 0, "new_state": 42},
    )
    await ingest_observation(obs, episodic, scorer, passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] sensor.power: 0 → 42"


@pytest.mark.asyncio
async def test_missing_old_state_is_rendered_as_unknown(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """StateChangedEvent.old_state is Optional — a first sighting has none."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "sensor.new_device", "new_state": "22.5"},
    )
    await ingest_observation(obs, episodic, scorer, passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] sensor.new_device: unknown → 22.5"


@pytest.mark.asyncio
async def test_entities_come_from_the_trigger_event(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("light.hallway"), episodic, scorer, passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.entities == ["light.hallway"]


@pytest.mark.asyncio
async def test_semantic_key_is_searchable(scorer: AsyncMock, passive_scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive("light.hallway", "off", "on"), episodic, scorer, passive_scorer
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.semantic_key == "Observed light.hallway change from off to on"


@pytest.mark.asyncio
async def test_the_entry_is_keyed_by_the_stable_observation_id(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """``RedisVectorStore.add`` HSETs at ``ctx:{id}`` — a fresh uuid duplicates on retry."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    obs = _passive()
    await ingest_observation(obs, episodic, scorer, passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.id == obs.observation_id


@pytest.mark.asyncio
async def test_reprocessing_an_observation_does_not_create_a_second_entry(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """The PEL reclaim pass makes retries real — an overwrite, not a duplicate.

    ``observation_id`` is generated at publish time and serialized into the
    stream, so it survives redelivery; a ``uuid4()`` minted per ingest attempt
    does not, and every reclaim would leave another copy behind at a new
    ``ctx:{id}`` key.
    """
    from core.memory.ingestor import ingest_observation

    store: dict[str, EpisodicEntry] = {}

    async def write(entry: EpisodicEntry, _significance: object) -> None:
        store[entry.id] = entry

    episodic = AsyncMock()
    episodic.write = AsyncMock(side_effect=write)

    # Round-trip through the wire the way a redelivered stream entry does.
    raw = _passive().model_dump_json()
    await ingest_observation(
        ReflexObservation.model_validate_json(raw), episodic, scorer, passive_scorer
    )
    await ingest_observation(
        ReflexObservation.model_validate_json(raw), episodic, scorer, passive_scorer
    )

    assert episodic.write.await_count == 2
    assert len(store) == 1, "a redelivered observation wrote a second episodic entry"


@pytest.mark.asyncio
async def test_passive_scorer_is_used_when_supplied(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """The passive population must be scored by the passive scorer."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive(), episodic, scorer, passive_scorer)

    passive_scorer.score.assert_awaited_once()
    scorer.score.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_observations_still_use_the_default_scorer(
    scorer: AsyncMock, passive_scorer: AsyncMock
) -> None:
    """Regression: the action path is untouched by the passive scorer."""
    from bus.schemas.events import ActionRequest, ActionResult
    from core.memory.ingestor import ingest_observation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
        action=action,
        result=ActionResult(
            source="home-service",
            request_id=action.request_id,
            tool_name="home.light_turn_on",
            status="success",
        ),
    )

    episodic = AsyncMock()
    await ingest_observation(obs, episodic, scorer, passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "home.light_turn_on" in entry.summary
    scorer.score.assert_awaited_once()
    passive_scorer.score.assert_not_awaited()


# ---------------------------------------------------------------------------
# The passive-scorer contract
# ---------------------------------------------------------------------------


def test_the_passive_scorer_has_no_silent_default() -> None:
    """Omitting it must be a type error, not a silent fallback to the shared scorer.

    At c6c9668 the sole production caller omitted ``passive_scorer`` and every
    passive observation was scored against ``ENTITY_FREQUENCY_KEY`` — driving
    ``novelty = 1/count`` toward zero for real reflex actions, the exact
    contamination this feature exists to prevent. Nothing raised, nothing
    logged, every test stayed green. A required parameter makes the omission
    visible to mypy at the call site.
    """
    import inspect

    from core.memory import ingestor

    for fn in (ingestor.ingest_observation, ingestor._ingest_entry, ingestor.run_ingestor):
        param = inspect.signature(fn).parameters["passive_scorer"]
        assert param.default is inspect.Parameter.empty, (
            f"{fn.__name__} still accepts an implicit passive_scorer"
        )


@pytest.mark.asyncio
async def test_ingesting_without_a_passive_scorer_is_a_type_error(scorer: AsyncMock) -> None:
    """The runtime half of the contract above."""
    from core.memory.ingestor import ingest_observation

    with pytest.raises(TypeError):
        await ingest_observation(_passive(), AsyncMock(), scorer)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Entry-point wiring
# ---------------------------------------------------------------------------


def test_ingestor_main_builds_a_passive_scorer_on_the_observed_key() -> None:
    """Guard the one wiring mistake that would fail silently in production."""
    import inspect

    from core.memory import ingestor_main

    source = inspect.getsource(ingestor_main.run)
    assert "OBSERVED_FREQUENCY_KEY" in source
    assert "passive_scorer" in source


@pytest.mark.asyncio
async def test_ingestor_main_forwards_a_scorer_bound_to_the_observed_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioural counterpart to the source-text guard above.

    That one greps ``run``'s source, so it stays green on the exact mistake it
    exists to catch — dropping ``frequency_key=`` while the explanatory comment
    still names ``OBSERVED_FREQUENCY_KEY``, or forwarding the shared scorer as
    ``passive_scorer=``. This one runs the wiring.
    """
    from core.memory import ingestor_main
    from shared.config import AlfredConfig
    from shared.streams import ENTITY_FREQUENCY_KEY, OBSERVED_FREQUENCY_KEY

    built: list[tuple[dict[str, object], object]] = []

    class RecordingScorer:
        def __init__(self, **kwargs: object) -> None:
            built.append((kwargs, self))

    forwarded: dict[str, object] = {}

    async def fake_run_ingestor(
        _redis: object,
        _episodic: object,
        scorer: object,
        passive_scorer: object,
        shutdown_event: object = None,
    ) -> None:
        forwarded["scorer"] = scorer
        forwarded["passive_scorer"] = passive_scorer

    monkeypatch.setattr(ingestor_main, "SignificanceScorer", RecordingScorer)
    monkeypatch.setattr(ingestor_main, "run_ingestor", fake_run_ingestor)
    monkeypatch.setattr(ingestor_main, "create_redis", lambda _url: AsyncMock())
    monkeypatch.setattr(ingestor_main, "RedisVectorStore", lambda **_kw: MagicMock())
    monkeypatch.setattr(ingestor_main, "SqliteVecStore", lambda **_kw: MagicMock())
    monkeypatch.setattr(ingestor_main, "EpisodicMemory", lambda **_kw: MagicMock())
    monkeypatch.setattr(ingestor_main, "start_warmup", lambda *_a, **_kw: MagicMock())

    await ingestor_main.run(AlfredConfig())

    assert len(built) == 2, "expected a shared scorer and a passive scorer"
    shared_kwargs, shared_scorer = built[0]
    passive_kwargs, passive = built[1]

    # The reflex-action scorer keeps the shared population.
    assert shared_kwargs.get("frequency_key", ENTITY_FREQUENCY_KEY) == ENTITY_FREQUENCY_KEY
    # The passive one counts against its own, or ~250 observations/day flatten
    # novelty (1/count) for real reflex actions too.
    assert passive_kwargs.get("frequency_key") == OBSERVED_FREQUENCY_KEY

    assert forwarded["scorer"] is shared_scorer
    assert forwarded["passive_scorer"] is passive
