"""Parking primitives: the approval id, the calls a gate deferred, the record that holds them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fakeredis
import pytest
from loguru import logger
from pydantic_ai import DeferredToolRequests, ToolApproved, ToolDenied
from pydantic_ai.messages import ToolCallPart

from bus.schemas.events import ActionRequest, ParkedCall, ParkedRun
from core.conscious.approvals import (
    APPROVAL_NAMESPACE,
    PARAM_KEY_MAX,
    PARAM_VALUE_MAX,
    REASON_CLAUSE_MAX,
    Decision,
    approval_id_for,
    claimable_run,
    confirmation_reply,
    describe_call,
    park,
    parked_calls,
    results_for,
    summary_of,
)
from core.conscious.effects import EffectLedger
from core.notifications.schema import Urgency
from core.routing.pending import (
    CONFIRMATION_TITLE,
    park_run,
    pending_key,
    read_pending,
    store_pending_action,
)

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


class FakeNotifier:
    """Records what would have been published."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(
        self,
        *,
        title: str,
        body: str,
        source: str,
        urgency: Urgency,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.published.append(
            {
                "title": title,
                "body": body,
                "source": source,
                "urgency": urgency,
                "metadata": metadata,
            }
        )


def _call(
    n: int = 0, tool: str = "home.unlock_door", reason: str | None = "Unlock the front door."
) -> ParkedCall:
    return ParkedCall(
        tool_call_id=f"call-{n}",
        tool_name=tool,
        target_service="home-service",
        parameters={"entity_id": f"lock.door_{n}"},
        reason=reason,
    )


class FailingNotifier:
    """A notifier whose publish fails."""

    async def publish(
        self,
        *,
        title: str,
        body: str,
        source: str,
        urgency: Urgency,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise ConnectionError("the dispatcher is down")


def _run(
    calls: list[ParkedCall] | None = None,
    identity: dict[str, Any] | None = None,
    approval_id: str = "appr-1",
    summary: str = "Unlock the front door; garage door open",
) -> ParkedRun:
    return ParkedRun(
        approval_id=approval_id,
        session_id="sess-1",
        channel="web_pwa",
        identity=identity if identity is not None else {"identity": "sir", "method": "passkey"},
        request_event_id="req-1",
        created_at=T0,
        content_type="text",
        tz_name="UTC",
        summary=summary,
        calls=calls if calls is not None else [_call(0), _call(1, "home.garage_door_open", None)],
        messages="[]",
    )


def test_the_approval_id_is_deterministic_and_differs_per_key() -> None:
    assert approval_id_for("req-1") == approval_id_for("req-1")
    assert approval_id_for("req-1") != approval_id_for("req-2")
    # A UUID5 under the module's own namespace, so no other id space can collide with it.
    assert len(approval_id_for("req-1")) == 36
    assert APPROVAL_NAMESPACE.version == 5


def test_parked_calls_read_the_gate_metadata() -> None:
    requests = DeferredToolRequests(
        approvals=[
            ToolCallPart("home_unlock_door", {"entity_id": "lock.front"}, "c1"),
            ToolCallPart("home_garage_door_open", {}, "c2"),
        ],
        metadata={
            "c1": {
                "tool_name": "home.unlock_door",
                "target_service": "home-service",
                "parameters": {"entity_id": "lock.front"},
                "reason": "x" * 600,
            },
            "c2": {
                "tool_name": "home.garage_door_open",
                "target_service": "home-service",
                "parameters": {},
                "reason": None,
            },
        },
    )

    first, second = parked_calls(requests)

    assert first == ParkedCall(
        tool_call_id="c1",
        tool_name="home.unlock_door",
        target_service="home-service",
        parameters={"entity_id": "lock.front"},
        reason="x" * 500,
    )
    assert (second.tool_call_id, second.tool_name, second.parameters, second.reason) == (
        "c2",
        "home.garage_door_open",
        {},
        None,
    )


def _tool(
    tool: str, parameters: dict[str, Any] | None = None, reason: str | None = None
) -> ParkedCall:
    """A call shaped like the real tools': ``target``, or ``domain``/``service``/``entity_id``."""
    return ParkedCall(
        tool_call_id="c1",
        tool_name=tool,
        target_service="home-service",
        parameters=parameters or {},
        reason=reason,
    )


def test_the_reply_names_every_call() -> None:
    calls = [_call(0, reason="Unlock the front door."), _call(1, "home.garage_door_open", None)]

    summary = summary_of(calls)

    assert summary == (
        'unlock door (entity_id=lock.door_0) ("Unlock the front door."); '
        "garage door open (entity_id=lock.door_1)"
    )
    assert confirmation_reply(summary) == (
        f"Before I do that, I need your confirmation: {summary}. Shall I, sir?"
    )


def test_a_targeted_tool_names_its_target_not_its_service() -> None:
    """I1: the real critical tools take ``target`` (an area, a name or an entity id)."""
    call = _tool(
        "home.lock_unlock", {"target": "Garage"}, "Unlock the front door for the dog walker"
    )

    assert describe_call(call) == (
        'lock unlock (target=Garage) ("Unlock the front door for the dog walker")'
    )


def test_call_service_names_the_service_it_will_call() -> None:
    """I1's original attack: a lock told to unlock, with a reason that says it will lock."""
    call = _tool(
        "home.call_service",
        {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"},
        "lock the front door for the night",
    )

    reply = confirmation_reply(summary_of([call]))

    assert reply == (
        "Before I do that, I need your confirmation: call service "
        "(domain=lock, service=unlock, entity_id=lock.front_door) "
        '("lock the front door for the night"). Shall I, sir?'
    )
    before_reason = reply.split(' ("')[0]
    assert "service=unlock" in before_reason
    assert "night" not in before_reason


def test_every_parameter_is_named_in_its_own_order() -> None:
    call = _tool("home.light_turn_on", {"target": "hall", "brightness": 40, "data": {"a": [1]}})

    assert describe_call(call) == "light turn on (target=hall, brightness=40, data={a:[1]})"


def test_a_parameter_that_is_not_text_is_compact_json() -> None:
    call = _tool(
        "home.cover_open",
        {"entity_id": ["cover.garage", "cover.gate"], "tilt": None, "fast": True, "note": "é"},
    )

    assert describe_call(call) == (
        "cover open (entity_id=[cover.garage,cover.gate], tilt=null, fast=true, note=é)"
    )


def test_a_parameter_named_reason_is_left_to_the_reason() -> None:
    call = _tool("home.lock_unlock", {"target": "Garage", "reason": "turn on the light"})

    assert describe_call(call) == "lock unlock (target=Garage)"


def test_a_call_with_no_parameters_has_no_parentheses() -> None:
    assert describe_call(_tool("home.garage_door_open")) == "garage door open"
    assert describe_call(_tool("home.garage_door_open", None, "Open it")) == (
        'garage door open ("Open it")'
    )


def test_a_value_is_one_short_line_without_quotes_or_format_characters() -> None:
    call = _tool(
        "home.lock_unlock",
        {"target": '  Front\n\n"door"\t\u202e\u200bGarage ' + "x" * 100},
    )

    described = describe_call(call)

    value = described.removeprefix("lock unlock (target=").removesuffix(")")
    assert value.startswith("Front door Garage xxx")
    assert value.endswith("…")
    assert len(value) == PARAM_VALUE_MAX
    assert "\n" not in described
    assert not any(ch in described for ch in '"“”„‟«»\u202e\u200b')


def test_a_plain_key_is_capped() -> None:
    described = describe_call(_tool("home.lock_unlock", {"k" * 100: "Garage"}))

    key = described.removeprefix("lock unlock (").removesuffix("=Garage)")
    assert key == "k" * (PARAM_KEY_MAX - 1) + "…"


def test_a_key_that_cleaning_would_change_is_escaped_instead() -> None:
    """``ta"rget`` must not read as ``target`` beside the real one."""
    call = _tool("home.lock_unlock", {'ta"rget': "Front door", "target": "Garage"})

    assert describe_call(call) == 'lock unlock (ta\\"rget=Front door, target=Garage)'


def test_a_look_alike_key_is_shown_escaped_not_cleaned() -> None:
    """P39: a zero-width character must not make a decoy key read as the real one."""
    call = _tool("home.lock_unlock", {"tar\u200bget": "Front door", "target": "Garage"})

    assert describe_call(call) == ("lock unlock (tar\\u200bget=Front door, target=Garage)")


@pytest.mark.parametrize(
    ("key", "shown"),
    [
        ("targ\u0435t", "targ\\u0435t"),  # a Cyrillic "e"
        ("tar\u202eget", "tar\\u202eget"),  # a bidi control
        ("tar\nget", "tar\\nget"),  # a newline
        ("t\U0001f512", "t\\ud83d\\udd12"),  # beyond the BMP
    ],
)
def test_a_key_that_is_not_plain_ascii_is_json_escaped(key: str, shown: str) -> None:
    assert describe_call(_tool("home.lock_unlock", {key: "Garage"})) == (
        f"lock unlock ({shown}=Garage)"
    )


def test_an_escaped_key_is_capped_like_any_other() -> None:
    described = describe_call(_tool("home.lock_unlock", {"\u0435" * 30: "Garage"}))

    key = described.removeprefix("lock unlock (").removesuffix("=Garage)")
    assert key.startswith("\\u0435\\u0435")
    assert key.endswith("…")
    assert len(key) == PARAM_KEY_MAX


def test_a_structured_value_is_shown_in_full() -> None:
    """P39: call_service's free ``data`` can carry the entity it acts on, so it is not cut."""
    data = {"pad": "x" * 80, "entity_id": "lock.front_door"}
    call = _tool("home.call_service", {"domain": "lock", "service": "unlock", "data": data})

    described = describe_call(call)

    assert described.endswith(",entity_id:lock.front_door})")
    assert "x" * 80 in described
    assert "…" not in described


def test_a_nested_look_alike_name_is_escaped_too() -> None:
    """The decoy that fails at the top level fails as well inside a value shown in full."""
    data = {"entity_id\u200b": "lock.back", "entity_id": "lock.front"}
    call = _tool("home.call_service", {"domain": "lock", "service": "unlock", "data": data})

    assert describe_call(call).endswith("data={entity_id\\u200b:lock.back,entity_id:lock.front})")


def test_a_long_list_is_shown_in_full_but_a_long_string_is_still_cut() -> None:
    ids = [f"light.room_{n}" for n in range(12)]

    listed = describe_call(_tool("home.light_turn_off", {"entity_id": ids}))
    text = describe_call(_tool("home.light_turn_off", {"entity_id": ",".join(ids)}))

    assert listed.endswith("light.room_11])")
    assert text.endswith("…)")
    assert "room_11" not in text


def test_a_reason_is_one_short_line_without_quotes() -> None:
    reason = '  Unlock the\n\n"front"\tdoor   ' + "x" * 200

    described = describe_call(_tool("home.lock_unlock", {"target": "Garage"}, reason))

    clause = described.removeprefix("lock unlock (target=Garage) (").removesuffix(")")
    assert clause.startswith('"Unlock the front door xxx')
    assert clause.endswith('…"')
    assert len(clause) == REASON_CLAUSE_MAX + 2  # the text is capped, the quotes are extra
    assert "\n" not in described
    assert '"' not in clause[1:-1]


def test_a_reason_cannot_fake_the_end_of_its_quotes() -> None:
    """Quote look-alikes, parentheses and bidi controls would close the clause early."""
    reason = (
        "Unlock the door\u201d (hall) \u201c\u202e\u2066 \u00abok\u00bb \u201efine\u201f "
        "\u2018a\u2019 \u2039b\u203a \u2033c\uff02 \u05f4d \u02ee" + "e"
    )

    described = describe_call(_tool("home.lock_unlock", {"target": "Garage"}, reason))

    clause = described.removeprefix("lock unlock (target=Garage) (").removesuffix(")")
    assert clause == '"Unlock the door hall ok fine a b c d e"'
    dropped = (
        '"()\u201c\u201d\u201e\u201f\u00ab\u00bb\u2018\u2019\u2039\u203a\u2033\uff02\u05f4\u02ee'
    )
    assert not any(ch in clause[1:-1] for ch in dropped + "\u202e\u2066")


def test_a_reason_cannot_open_a_second_parameter_group() -> None:
    """P39: ``″) (target=Front door) (″`` read as a group of its own next to the real one."""
    call = _tool("home.lock_unlock", {"target": "Garage"}, "front door″) (target=Front door) (″ok")

    described = describe_call(call)

    assert described.count("(target=") == 1
    assert described.startswith("lock unlock (target=Garage) (")
    assert described.count("(") == 2  # the parameters' and the reason's, no more
    assert described.count(")") == 2


def test_a_value_keeps_its_apostrophes_and_parentheses() -> None:
    """Only the reason loses them: a name like this is what the house resolves."""
    call = _tool("home.lock_unlock", {"target": "Sam\u2019s room (upstairs)"})

    assert describe_call(call) == "lock unlock (target=Sam\u2019s room (upstairs))"


def test_a_reason_of_only_quotes_and_space_adds_no_clause() -> None:
    assert describe_call(_tool("home.lock_unlock", {"target": "Garage"}, ' "" \n ')) == (
        "lock unlock (target=Garage)"
    )


def test_a_reason_at_the_cap_is_kept_whole() -> None:
    reason = "y" * REASON_CLAUSE_MAX

    described = describe_call(_tool("home.lock_unlock", {"target": "Garage"}, reason))

    assert described.endswith(f'("{reason}")')


@pytest.mark.parametrize(
    ("decision", "denial"),
    [
        ("approve", None),
        ("deny", "The user declined."),
        ("other", "Not confirmed; the user moved on."),
    ],
)
def test_results_cover_every_call_with_the_verdict(decision: Decision, denial: str | None) -> None:
    run = _run()

    results = results_for(run, decision)

    assert set(results.approvals) == {"call-0", "call-1"}
    for verdict in results.approvals.values():
        if denial is None:
            assert isinstance(verdict, ToolApproved)
        else:
            assert isinstance(verdict, ToolDenied)
            assert verdict.message == denial


async def test_park_notifies_once_and_ledgers_each_call(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    notifier = FakeNotifier()
    run = _run()

    first = await park(redis, notifier, run, ledger_key="req-1")
    second = await park(redis, notifier, run, ledger_key="req-1")

    assert first == second == run
    assert await redis.exists(pending_key("appr-1")) == 1
    assert len(notifier.published) == 1
    note = notifier.published[0]
    assert note["title"] == CONFIRMATION_TITLE
    assert note["body"] == "Alfred wants to run 'home.unlock_door' on home-service — confirm?"
    assert note["source"] == "conscious-engine"
    assert note["urgency"] is Urgency.URGENT
    assert note["metadata"] == {
        "pending_action_id": "appr-1",
        "tool_name": "home.unlock_door",
        "parameters": {"entity_id": "lock.door_0"},
        "reason": "Unlock the front door; garage door open",
    }
    effects = await EffectLedger(redis).read("req-1")
    # A repeated park leaves the ledger as it was: one parked entry per call.
    assert [(e.tool, e.args, e.status) for e in effects] == [
        ("home.unlock_door", {"entity_id": "lock.door_0"}, "parked"),
        ("home.garage_door_open", {"entity_id": "lock.door_1"}, "parked"),
    ]


async def test_park_recovers_a_crash_between_the_record_and_the_ledger(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """The record was written, then the process died: no push went out, the ledger is empty."""
    notifier = FakeNotifier()
    run = _run()
    assert await park_run(redis, run)

    parked = await park(redis, notifier, run, ledger_key="req-1")

    assert parked == run
    assert len(notifier.published) == 1
    effects = await EffectLedger(redis).read("req-1")
    assert [(e.tool, e.status) for e in effects] == [
        ("home.unlock_door", "parked"),
        ("home.garage_door_open", "parked"),
    ]

    await park(redis, notifier, run, ledger_key="req-1")

    assert len(notifier.published) == 1
    assert await EffectLedger(redis).read("req-1") == effects


async def test_park_retries_a_push_that_failed(redis: fakeredis.FakeAsyncRedis) -> None:
    run = _run()

    with pytest.raises(ConnectionError):
        await park(redis, FailingNotifier(), run, ledger_key="req-1")

    # The record stands, so the Door shows it, but nothing says the owner was told.
    assert await read_pending(redis, "appr-1") == run
    assert await EffectLedger(redis).read("req-1") == []

    notifier = FakeNotifier()
    await park(redis, notifier, run, ledger_key="req-1")

    assert len(notifier.published) == 1
    assert len(await EffectLedger(redis).read("req-1")) == 2


async def test_park_answers_from_the_stored_record_when_the_calls_differ(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """A redelivered turn's model can defer other calls than the record the owner will approve."""
    stored = _run(calls=[_call(7, "home.lock_door", "Lock the back door.")], summary="Lock it")
    assert await park_run(redis, stored)
    notifier = FakeNotifier()

    parked = await park(redis, notifier, _run(), ledger_key="req-1")

    assert parked == stored
    [note] = notifier.published
    assert note["body"] == "Alfred wants to run 'home.lock_door' on home-service — confirm?"
    assert note["metadata"] == {
        "pending_action_id": "appr-1",
        "tool_name": "home.lock_door",
        "parameters": {"entity_id": "lock.door_7"},
        "reason": "Lock it",
    }
    assert [(e.tool, e.status) for e in await EffectLedger(redis).read("req-1")] == [
        ("home.lock_door", "parked")
    ]
    # The record the owner will approve is untouched.
    assert await read_pending(redis, "appr-1") == stored


async def test_park_keeps_its_own_run_when_the_stored_record_is_not_a_parked_run(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """The id is taken by another kind of record: there is no stored run to answer from."""
    other = ActionRequest(
        source="conscious-engine",
        request_id="appr-1",
        target_service="home-service",
        tool_name="home.lock_door",
        parameters={},
    )
    await store_pending_action(redis, other)
    notifier = FakeNotifier()
    run = _run()

    assert await park(redis, notifier, run, ledger_key="req-1") == run
    assert notifier.published[0]["metadata"]["tool_name"] == "home.unlock_door"


async def test_a_guest_record_is_not_claimable(redis: fakeredis.FakeAsyncRedis) -> None:
    notifier = FakeNotifier()
    guest = _run(identity={"identity": "guest", "method": "none"})
    await park(redis, notifier, guest, ledger_key="r")
    await park(redis, notifier, _run(approval_id="appr-2"), ledger_key="r2")

    assert await read_pending(redis, "appr-1") is not None
    assert await claimable_run(redis, "appr-1") is None
    claimable = await claimable_run(redis, "appr-2")
    assert claimable is not None and claimable.approval_id == "appr-2"
    assert await claimable_run(redis, "missing") is None


async def test_an_unreadable_record_is_not_claimable(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.set(pending_key("appr-1"), b"not a pending record", ex=300)

    assert await claimable_run(redis, "appr-1") is None


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        (b"not a pending record", "ValidationError"),
        (
            b'{"kind": "parked_run", "approval_id": "appr-1", "calls": [{"parameters": '
            b'{"target": "hunter2"}}], "summary": 7}',
            "ValidationError",
        ),
    ],
)
async def test_an_unreadable_record_is_logged_by_type_only(
    redis: fakeredis.FakeAsyncRedis, raw: bytes, kind: str
) -> None:
    """The record holds the owner's history and the calls' parameters, and a validation
    message quotes the input, so the log names the kind of failure and nothing else (P37)."""
    await redis.set(pending_key("appr-1"), raw, ex=300)
    records: list[Any] = []
    sink_id = logger.add(lambda message: records.append(message.record), level="WARNING")
    try:
        await claimable_run(redis, "appr-1")
    finally:
        logger.remove(sink_id)

    (record,) = records
    assert "appr-1" in record["message"]
    assert kind in record["message"]
    assert "hunter2" not in record["message"]
    assert record["exception"] is None  # no traceback: loguru renders frame values in one
