# Attribution — who did it

Every change in the house that passes the availability bridge says who caused it: a person,
Alfred, an automation, or something outside Alfred's view. (The bridge drops a device going
unavailable and a brief drop-out that comes straight back; those are never counted.) The cause
is read from the `context` Home Assistant attaches to each state change, counted by day, and
kept with the calls Alfred made, so a private report can answer "what did Alfred change, and
why?".

**Status.** Alfred's side (reading the context, counting, logging, the report) ships first.
home-service's side (forwarding the context, returning each call's context id, connecting as a
Home Assistant user of its own) is the design of its next release. Until that release every
change reads `unknown`; see [An older home-service](#an-older-home-service).

Design: `docs/superpowers/specs/2026-10-24-reflex-anticipation-design.md` (slice 1, "who did
it") · The first slice of the Reflex anticipation work, which replaces
[#286](https://github.com/anirudhlath/alfred/issues/286) and
[#287](https://github.com/anirudhlath/alfred/issues/287); its slice 2 is PR 8 of
[#317](https://github.com/anirudhlath/alfred/issues/317).

## Why

Reflex decides what to do about each change in the house. Without a cause it cannot tell the
owner turning a lamp off from Alfred having just turned it off, so it proposes to undo what
Alfred did a minute ago. The shadow log of
[#285](https://github.com/anirudhlath/alfred/issues/285) shows that: close to half its
proposals would reverse the change that had just happened.

Slice 1 only records the cause and shows it. Nothing it reads changes what Reflex proposes;
shadow mode stays, and nothing Reflex proposes executes. Two things build on it:

- **Oversight.** The owner can see what Alfred changed in a window, which part of Alfred asked
  for it (Reflex, System 2, a trigger or the admin API) and the reason it gave.
- **Learning.** From slice 3 on, habit detection counts only `person` changes, so Alfred never
  learns its own actions as the owner's habits. The Librarian reads no state changes today, so
  that rule has no code to live in yet.

## Data flow

```mermaid
sequenceDiagram
    participant HA as Home Assistant
    participant HS as home-service
    participant R as Redis
    participant Rt as Reflex runner
    participant E as Reflex engine
    participant D as DomainRouter
    participant Rep as Report

    Note over HS: With home-service's next release: it forwards the context<br/>and returns each call's context id. Until then both are absent.
    HA-->>HS: state_changed (with context)
    HS->>R: XADD alfred:home:state_changed (StateChangedEvent.context)
    R->>Rt: XREADGROUP
    Rt->>R: SET NX alfred:attribution:counted:{entry_id}
    Rt->>R: HINCRBY alfred:attribution:causes:{UTC date} {cause}
    opt person, Alfred or automation
        Rt->>R: XADD alfred:attribution:changes
    end
    Rt->>E: event (cause in the prompt line)
    E-->>Rt: Decided (proposal, outcome, the exact prompt)
    Rt->>R: XADD alfred:reflex:decision_log (prompt, cause, proposal, outcome)
    D->>HS: ActionRequest (executed, through HomeAgent)
    HS-->>D: ActionResult (context_id)
    D->>R: XADD alfred:attribution:calls
    Rep->>R: XRANGE calls and changes, HGETALL causes
    Note over Rep: joins a call to its changes on the context id
```

## The four causes

`core/attribution/cause.py` reads a `ChangeContext` and returns one cause. The checks run in
order and the first match wins.

| Order | Test on the context | Cause | Means |
|---|---|---|---|
| 1 | no context at all | none (`None`) | An older home-service, or a replayed old entry: nothing is known. The counter field is `unknown`. |
| 2 | `by_service` is set | `alfred` | home-service's own call, made on Alfred's request. Wins over any user or parent id on the same context. |
| 3 | `user_id` is set | `person` | Someone used an app or a dashboard. |
| 4 | `parent_id` is set | `automation` | A script or an automation ran the call. |
| 5 | otherwise | `outside` | A wall switch, a device's own app or schedule, a voice assistant, or Home Assistant itself. |

`ChangeContext` is the event's `context` field: `id`, `user_id`, `parent_id`, `person` (the
person entity of that user, when home-service could map it) and `by_service`. Its fields are in
[event-bus.md](event-bus.md#statechangedevent) and the publisher's side in
[sdk.md](sdk.md#saying-who-caused-a-state-change).

### The copy

The cause is phrased the same way in Reflex's prompt, in episodic memory and in the reports:

| Cause | Words |
|---|---|
| `person`, named | `by Alex` |
| `person`, not named | `by a person` |
| `alfred` | `by Alfred` |
| `automation` | `by an automation` |
| `outside` | `by a switch, app or device` |
| none | nothing: the line reads exactly as it did before attribution |

`cause_words(context, name=None)` builds it. A caller that can resolve a display name passes it;
otherwise a person is named by the context's person entity, and without one, "a person".

### Where the cause appears

| Place | Shows | Person named by |
|---|---|---|
| Reflex's `## What changed` line | `Bedroom Lamp: on → off · by Alex` | the person's friendly name in live state |
| Episodic memory's summary of the observation | `…, by Alfred` at the end | the person entity id |
| The shadow report | the same line as the prompt, `· by person.alex` | the person entity id |
| The decision log | `cause` on each record, and the event with its whole context | not named: the cause, and the context's person entity |

The shadow report and memory have no live state to look a friendly name up in, so they name a
person by entity id (`by person.alex`) where Reflex's prompt uses the friendly name. Neither
changes the cause.

## Data models

`ChangeContext` (`bus/schemas/events.py`, mirrored in `sdk/alfred_sdk/events.py`), the optional
`context` of a `StateChangedEvent`. Every field is optional.

| Field | Type | Meaning |
|---|---|---|
| `id` | `str \| None` | The platform's context id (Home Assistant's `context.id`) |
| `user_id` | `str \| None` | The platform user who caused the change |
| `parent_id` | `str \| None` | Set when an automation or a script caused it |
| `person` | `str \| None` | The person entity of that user, when the publisher could map it |
| `by_service` | `bool` (default `false`) | The publishing service's own call caused it, on Alfred's request |

`AlfredCall` (`core/attribution/log.py`), one entry of `alfred:attribution:calls`:

| Field | Type | Meaning |
|---|---|---|
| `time` | `datetime` | When Alfred made the call |
| `source` | `str` | The part of Alfred that asked (`ActionRequest.source`): `reflex-engine`, `conscious-engine`, `trigger-engine` or `admin-api` |
| `request_id` | `str` | The `ActionRequest`'s id |
| `target_service` | `str` | The service the call went to |
| `tool_name` | `str` | The tool called |
| `parameters` | `dict[str, Any]` | The call's parameters |
| `reason` | `str \| None` | Why the actor wanted it, when it said |
| `context_id` | `str \| None` | The context id the service returned for the call; `None` from an older home-service |

`AttributedChange` (`core/attribution/log.py`), one entry of `alfred:attribution:changes`:

| Field | Type | Meaning |
|---|---|---|
| `time` | `datetime` | The event's timestamp |
| `entity_id` | `str` | The entity that changed |
| `old_state` | `str \| None` | Its previous state (`None` on a first report) |
| `new_state` | `str` | Its new state |
| `cause` | `person \| alfred \| automation` | Who caused it |
| `context` | `ChangeContext` | The whole context, kept for the join |

`DecisionRecord` (`core/reflex/decision_log.py`), one entry of `alfred:reflex:decision_log`:

| Field | Type | Meaning |
|---|---|---|
| `time` | `datetime` | When Reflex decided |
| `origin` | `state_change \| trigger_fired` | What Reflex was asked about |
| `event` | `dict[str, Any]` | The event, dumped as JSON (a state change keeps its whole context) |
| `cause` | `Cause \| None` | Who caused a state change; `None` for a trigger or an event with no context |
| `prompt` | `str` | The exact prompt the model saw |
| `proposal` | `ReflexProposal` | What Reflex proposed |
| `outcome` | `ReflexOutcome \| None` | How the decision ended (`none`, `invalid`, `would_reverse`, `shadow`, ...); `None` on a record written before outcomes |
| `recent` | `dict[str, PersonChange]` | What people had just changed, by entity ID, when Reflex decided; `{}` when nothing, and on an older record |

## What is kept

All of it is private bookkeeping, in Redis, and every write is best-effort: a refused write
(Redis at its memory limit, say) logs one warning and never raises into routing or Reflex, never
changes a decision and never blocks an ACK.

| Key | Type | Holds | Retention |
|---|---|---|---|
| `alfred:attribution:calls` | Stream | One `AlfredCall` per successful call Alfred made: time, `source` (the part of Alfred that asked), request id, target service, tool, parameters, reason, and the `context_id` the service reported | up to 30 days or 20,000 entries, whichever comes first |
| `alfred:attribution:changes` | Stream | One `AttributedChange` per state change whose cause is `person`, `alfred` or `automation`, with its whole context | up to 30 days or 50,000 entries, whichever comes first |
| `alfred:attribution:causes:<UTC date>` | Hash | One counter per cause (`person`, `alfred`, `automation`, `outside`) plus `unknown`, for every change that day that passes the availability bridge | 30 days (TTL) |
| `alfred:attribution:counted:{entry_id}` | String | A marker that a `alfred:home:state_changed` entry has been counted | twice the replay window (`REPLAY_MARKER_TTL_SECONDS`, 10 minutes) |
| `alfred:reflex:decision_log` | Stream | One `DecisionRecord` per decision that reached the model: the event, its cause, the exact prompt, the proposal, its outcome and what people had just changed | up to 30 days or 10,000 entries, whichever comes first |
| `alfred:reflex:decided:{event_id}` | String | A marker that Reflex has decided an event, so a redelivered one is not inferred again (`SET NX EX`) | twice the replay window (`REPLAY_MARKER_TTL_SECONDS`, 10 minutes) |

- A change from a switch, an app or a device (`outside`) is counted but not kept in the changes
  stream: there is nobody to attribute it to.
- The `counted:` marker is why a redelivered entry is counted once. Reflex's loop reclaims an
  entry whose inference failed (a model outage is exactly when that happens) and processes it
  again; the first delivery's marker, set with `SET NX`, tells the second to write nothing. Its
  TTL is the same one the availability bridge keeps its decisions for. `record_change` called
  with no entry id (a test, a one-off) counts every time.
- Age is enforced with `XTRIM MINID` on each append (`shared/redis_streams.append_capped`), in
  the same pipeline as the `XADD`, because approximate `MAXLEN` trimming never ages out a small
  stream.
- The decision log is what slice 2's replay set and model comparison are built from. Read it by
  time with `DecisionLog.read(since=, until=)` (timezone-aware bounds; a record is kept when
  `since <= time < until`, `until` defaulting to now). That is how
  `python -m core.reflex.replay run --since ...` reads it; see
  [reflex-anticipation.md](reflex-anticipation.md#replay).
- The `decided:` marker makes a second decision about one event unlikely, so the log holds
  about one record per event. The runner checks it after the attention gate and before the
  engine, and a hit is ACKed without asking the model again; it is set only after the decision
  has been counted and published, so a crash before that decides the event again and records
  it twice, and a failed inference leaves no marker. A `TriggerFired` uses its own `event_id`.
  The replay drops the duplicates, keeping each `event_id`'s first record.

### Joining a call to its changes

A call is logged when `DomainRouter.route` has executed it, with the `context_id` home-service
returned in its result. A change belongs to a call when its `context.id` equals the call's
`context_id` (the call's own effect, or a script it ran) or its `context.parent_id` does (an
automation the call set off). The state change can reach Alfred before the call's result does;
nothing depends on the order, because the report joins them afterwards.

## The report

```bash
docker exec alfred python -m core.attribution.report --days 7
```

`core/attribution/report.py` prints Markdown in the user's timezone, oldest first. It names people
and devices, so it is read privately and never posted to a public thread.

1. **Changes by cause.** One row per UTC day in the window, with the five counter fields. The
   first and last rows are whole UTC days, so they can include counts just outside the window.
2. **What Alfred changed.** One bullet per call: time, the part of Alfred (`Reflex`, `System 2`,
   `a trigger`, `the admin API`; an unknown source is shown as it is), the tool and its
   parameters, and the reason in quotes when it gave one. Under it, each change that belongs to
   it as `entity: old → new · by whom`. A call with no `context_id` says `no context id (older
   home-service)`; one whose id matches nothing says `no change seen`.
3. **Changes by people and automations.** Every `person` or `automation` change that belongs to
   no call in the window, with its cause.

`--days` must be at least 1. Each stream keeps up to 30 days or its cap (calls 20,000, changes
50,000, decision log 10,000), whichever comes first, and the day-counter hashes keep 30 days, so a
longer window still shows only the last 30. A busy house can fill the changes stream before 30
days pass, so "no change seen" can mean the change was trimmed from the changes stream.

When a change carrying a call's own context id has a cause other than `alfred`, the report opens
with one line before its heading ("N changes matched ... but were not", singular for one):

> 1 change matched Alfred's calls but was not attributed to Alfred: home-service is not
> connected to Home Assistant as a user of its own.

That line is the operator step below, not yet taken. A script's or an automation's children
(matched on `parent_id`) are rightly an automation's work and do not raise it.

## Operator requirement

Home Assistant records the user who made each call. The design that ships with home-service's
next release has it connect to Home Assistant as **a user of its own**, not as the owner: it asks
Home Assistant which user its token belongs to, and marks `by_service` on every change that user
caused. If it were to use the owner's token, every change Alfred makes would carry the owner's
user id, nothing would be marked as Alfred's, and Alfred's changes would read as a person's until
the token is replaced. The report flags exactly this. Creating that user is a Home Assistant admin
step, taken when that release is deployed.

## An older home-service

Alfred core ships before home-service sends a context. Until then, and for any entry replayed
from before, an event has no `context`: it parses and flows everywhere as it always did, the
cause is `None`, the prompt line, the memory summary and the shadow log read as they did, and the
day's counter says `unknown`. A call whose result carries no `context_id` is logged all the same,
and the report says `no context id (older home-service)` for it.

## Files

| File | Role |
|---|---|
| `bus/schemas/events.py`, `sdk/alfred_sdk/events.py` | `ChangeContext` and `StateChangedEvent.context` |
| `core/attribution/cause.py` | `cause_of()`, `cause_words()`, `ATTRIBUTED` |
| `core/attribution/log.py` | `AlfredCall`, `AttributedChange`, `record_call()`, `record_change()`, `causes_key()` |
| `core/attribution/report.py` | The report: `build_report()`, `PARTS` |
| `core/reflex/decision_log.py` | `DecisionLog` (`record()`, `read(since=, until=)`) |
| `core/reflex/replay.py` | `python -m core.reflex.replay`, which decides recorded prompts again and prints counts |
| `core/routing/domain_router.py` | calls `record_call()` after an executed action |
| `core/reflex/runner.py` | calls `record_change()` after the availability bridge, before the attention gate |
| `shared/streams.py` | the stream and key names, caps and `ATTRIBUTION_MAX_AGE` |
