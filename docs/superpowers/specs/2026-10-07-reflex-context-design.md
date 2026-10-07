# Reflex Context — The House by Room, the Event in One Line, Decisions in Shadow

**Status:** Draft, for review
**Date:** 2026-10-07
**Issue:** [#285](https://github.com/anirudhlath/alfred/issues/285) (slice 1 of
[#285](https://github.com/anirudhlath/alfred/issues/285) →
[#286](https://github.com/anirudhlath/alfred/issues/286) →
[#287](https://github.com/anirudhlath/alfred/issues/287)). Rooms come from
[alfred-home-service#25](https://github.com/anirudhlath/alfred-home-service/issues/25).

## Problem

Reflex is slow, and it cannot make the calls it exists to make.

- **Prompt size.** Each Reflex call sends about 14,400 tokens. The Home State block,
  every one of the 493 entities in live state rendered one per line, is 72% of it. That
  includes about 220 sensors, 60 buttons and 20 `notify` targets. The tool section lists
  every light and speaker inside each tool's `target` description. The event carries raw
  Home Assistant attributes such as `entity_picture` tokens and `supported_features`.
- **Every call is a cache miss.** [#283](https://github.com/anirudhlath/alfred/pull/283)
  removed `ContextReader`'s 5-minute cache on purpose, so Reflex reads fresh state. The
  Home State block now differs on every call, since the entity that just changed is in it.
  vLLM's prefix cache therefore never covers more than the system prompt. Measured on
  2026-10-07, sending the same 14,411-token prompt took 926 ms cold and 79 ms on a cache
  hit. Calls are serial, so bursts stack: a 12-light change at 03:51 reached 2.6 s.

  | Observation latency (event → observation) | Nothing in the previous 5 min | Within 5 min of another event |
  |---|---|---|
  | 2026-09-25 → 10-06 | p50 ~800 ms | p50 ~100–300 ms, mostly under 200 ms |
  | after #283 deployed | p50 859 ms | p50 1,425 ms, 0 of 17 under 200 ms |

  The admin overview's `reflex.p50_ms` reads about 1,070 ms, and the target in
  `.claude/rules/core/reflex-engine.md` is under 500 ms.
- **It cannot judge.**
  - There is no local time in the prompt; `sun.sun` sits near line 590.
  - Live-state entries carry no room, so a lamp named "Ikea Lamp 2" has no location.
  - The rules say "only act if the event clearly matches a user preference", and
    `learned.md` holds one line, about notifications.

  Of 6,992 observations, 18 carry an action. All 18 are from August, and all are "media
  player starts playing → living room lights to 40%". Since 2026-08-31 Reflex has spent
  about a second of GPU time per event to answer `{"action": "none"}`.

## Goal

Reflex sees, in under ~1,000 tokens, what a person would need in order to judge the event:

- the time;
- who is home;
- what is on in each room;
- the change itself, in one readable line.

Reflex records what it *would* do (act, ask or none, with a reason) without executing
anything, so production data shows whether its common sense holds up before slice 2
gives it autonomy.

## Decisions taken with the owner

- **What Reflex is for.** It makes common-sense judgment calls and acts on learned habits.
  "The whole goal with Reflex is that it automatically figures out what I need." The
  owner's example calls:
  - everyone has left, or it is bedtime, and lights or the TV are still on;
  - the TV starts playing at night → dim the room;
  - someone arrives home after dark → lights on;
  - nobody is home → climate setback.
- **Autonomy, for slice 2.** Obvious calls get done and the owner is told afterwards, with
  an undo. Unsure calls arrive as a one-tap suggestion. Answers become preferences
  (slice 3).
- **Structural selection, not retrieval.** Embedding search over entities was rejected.
  Similar text is not the same as relevant ("Apple TV playing" does not retrieve "sun
  down" or who is home), and a ranking that shifts per event defeats the prefix cache.
  The actionable house is small enough to show whole. Retrieval belongs to memory, in
  slice 3.
- **Rooms matter, and the SDK stays service-agnostic.** Home-service not sending rooms is
  a bug, alfred-home-service#25. The room is service-specific detail, so it rides in
  `attributes` the way `friendly_name` already does. There is no SDK field. Alfred
  documents the attribute keys it understands so any service can provide them.
- **Labels, not confidence numbers.** The model chooses act, ask or none. Its
  self-reported confidence numbers are poorly calibrated.
- **The PRD eval suite owns evals.** The old `evals` runner is being replaced
  (`docs/superpowers/specs/2026-10-06-prd-eval-suite-design.md`). This slice adds no
  harness work. Its scenarios are written below as goldens for the PRD suite's `reflex`
  suite.

## Non-goals

- **Executing anything.** Slice 2 (#286) adds act and ask.
- **Learning from answers or retrieving memory.** That is slice 3 (#287).
- **The Conscious engine.** It keeps `ContextReader` and its full-house render, and it
  picks up rooms automatically because it renders every attribute.
- **Contract C9.** Reflex keeps seeing only `audience == "reflex"` tools: light, switch,
  media player and scene. The climate example stays out of reach in this slice; #286
  decides whether an *ask* may propose a conscious-audience tool, since the owner confirms
  every ask anyway.
- **Restoring a context cache.** #283 removed it so Reflex never reads 5-minute-old state.
  This design gets its speed from size and ordering instead.
- **Attention-gate changes.**

## Design

### 1. The prompt

A new module, `core/reflex/prompt.py`, builds the prompt. It is pure: data goes in, a
string comes out, with no I/O. `ReflexEngine` gathers the inputs and calls it. The
sections run from most stable to least, so the cacheable prefix is as long as possible:

```
[fixed]   Role, rules, decision format, tools
[slow]    ## Preferences
[minute]  ## Now
[event]   ## House
          ## What changed
          ## Decision (JSON only):
```

An illustrative render (names are placeholders):

```
## Now
Tue 7 Oct, 22:57 (night) · sun down
People: Person A home · Person B Work

## House
Living Room: Couch Overhead on 40% · Candle 1 off · Candle 2 off · Apple TV playing "<title>" · Soundbar on
Bedroom: Bed Left off · Bed Right off · Echo Dot idle
Office: Desk Left on 70% · Desk Right on 70%
Other: Thermostat heat 68° (now 66°) · Vacuum docked

## What changed
Living Room Apple TV (Living Room): paused → playing · "<title>" · <app>

## Decision (JSON only):
```

**Fixed section.**

```
You are Alfred's Reflex Engine, the quiet steward of a home. One thing in the house just
changed. Decide whether to do something about it.

- act: the right move is obvious. Common sense or a stated preference makes it plainly
  what the household wants, and doing it would surprise no one at home.
- ask: a move is plausible, but you are not sure it is wanted.
- none: nothing needs doing. This is the usual answer.

Use only the tools below. Target a room by its name in the House section, or a device by
its name.

Respond with JSON only. Either {"decision": "none"} or
{"decision": "act" | "ask", "reason": "<one short sentence>", "tool_name": "...",
 "target_service": "...", "parameters": {...}}
```

Tools render as one line each, `- home.light_turn_on(target, brightness_pct)
[home-service]: light.turn_on`. Parameter descriptions are dropped, because home-service
puts the full entity lists there. They are still built from the tool registry, never
hardcoded.

**Preferences.** These come from `MemoryReader.get_preferences()`, as today.

**Now.**
- Local date, time and weekday in the user's timezone (`shared.usertime.get_user_timezone`:
  stored, then env, then UTC), plus a coarse label: morning 05–12, afternoon 12–17,
  evening 17–21, night 21–05.
- `sun.sun` as "sun up" or "sun down".
- Each `person.*` entry as its name and state (`home`, `not_home` or a zone name).

**House.**
- **What it lists:** live-state entries with `kind == "controllable"` whose domain is in
  `HOUSE_DOMAINS = {light, media_player, switch, climate, fan, cover, lock, vacuum}`. This
  is a module constant, documented as a rendering choice.
- **Grouping:** one line per room, keyed by `attributes.area`. Rooms are sorted, and
  entities with no room come last under "Other".
- **Each entity:** its name (`attributes.friendly_name`, else the entity ID) and state,
  plus at most one detail:
  - brightness as a percentage for a light that is on;
  - `media_title` for a playing media player;
  - target and current temperature for climate.
- **When live state is unavailable:** the section reads `Live home state unavailable.`,
  as `ContextReader` does today.
- **Size:** measured on 2026-10-07, this is 79 entities plus people, about 650 tokens.

**What changed.**
- One line: `<name> (<room>): <old> → <new>`, then up to three details from the event's
  own attributes, from an allowlist: `media_title`, `media_artist`, `app_name`,
  `brightness`, `temperature`, `current_temperature` and `hvac_action`.
- The name and room come from the entity's live-state entry. An entity with no entry
  (for example a door sensor) renders with its entity ID and no room.

**TriggerFired** uses the same Now and House sections. In place of What changed it shows
`Trigger fired: <name> (<type>)` plus its context. The fixed text adds that the owner is
already being notified, as the current trigger prompt does.

**Inference.** `openai_client.infer` gains `max_tokens=150` and keeps `temperature=0`
and `response_format=json_object`. A "none" costs about 7 output tokens. A reason costs
about 40 tokens, roughly 230 ms at ~175 tokens/s, which is paid only on act or ask.

### 2. Rooms and the well-known attributes

Home-service sets `attributes.area` (alfred-home-service#25), and the SDK does not
change. `docs/live-state.md` gains a **Well-known attributes** section for service
authors:

| Key | Meaning | Alfred uses it for |
|---|---|---|
| `friendly_name` | The display name | Every prompt line |
| `area` | Where the entity is, as a name | House grouping, room targeting |
| `unit_of_measurement` | The unit of `state` | Rendering numbers |
| `device_class` | What kind of sensor or device | Attention seeding, rendering |

All keys are optional. Alfred uses them when present and falls back to the entity ID
otherwise. The section includes a short how-to: "Help Alfred understand your entities."

This slice ships before #25. Until #25 deploys, every entity renders under "Other".

### 3. The decision contract

`ReflexEngine.process_event` and `process_trigger_fired` return a `ReflexProposal`
instead of an `ActionRequest | None`:

```python
class ReflexProposal(BaseModel):
    decision: Literal["act", "ask", "none", "invalid"]
    reason: str | None = None
    action: ActionRequest | None = None   # proposed only; never executed in this slice
    raw: str | None = None                # the model's text, kept when decision == "invalid"
    problem: str | None = None            # why it is invalid
```

Parsing turns the model's output into a proposal:

- `{"decision": "none"}`, or the legacy `{"action": "none"}`, becomes **none**.
- act or ask with a registered `tool_name` and a valid `target_service` becomes a
  proposal with an `ActionRequest` whose `reason` is the model's reason, so slice 2's
  confirmation prompt can show it.
- Anything else becomes **invalid**, with `raw` and a `problem`:
  - text that is not JSON;
  - an unknown decision;
  - act or ask without a tool;
  - an unregistered service, or a tool outside Reflex's audience.

### 4. Shadow recording

Nothing executes. Both paths stop calling `DomainRouter.execute_action`: the state-change
path in `runner.process_stream_entry` and the TriggerFired path in `__main__`. The router
stays wired for slice 2.

- **act, ask and invalid** are always published to `alfred:reflex:observations` as a
  `ReflexObservation` with the new optional field `proposal: ReflexProposal | None`.
  `action` and `result` stay `None`, because those fields mean "this happened". These
  bypass the per-entity observation debounce; they are rare and are the evidence this
  slice exists to collect.
- **none** keeps today's path. On the state-change path that is `observe_passively`,
  debounced per entity, with no proposal. The TriggerFired path records nothing for
  none, as today.
- **Counts.** Each decision does `HINCRBY alfred:reflex:decisions:<YYYY-MM-DD> <decision> 1`
  with a 30-day TTL, so the report has true totals even though "none" observations are
  debounced.
- **Memory.** The Memory Ingestor ignores `proposal`. Episodic memory keeps recording
  "seen, not acted on", so neither the Librarian's pattern detection nor the Conscious
  engine ever treats something Reflex only proposed as something that happened. Because
  act, ask and invalid skip the debounce, their events reach memory even inside an
  entity's debounce window. They are rare enough that this does not flood it.

### 5. The shadow report

`python -m core.reflex.shadow_report --days 7` (run with `docker exec`) prints Markdown,
ready to post to #285:

- daily counts per decision, from the counters;
- every act, ask and invalid proposal in the window, each with its time, the
  re-rendered What changed line, the decision, the reason, and the proposed tool and
  parameters.

The owner marks each one right or wrong in a comment. Those verdicts decide which kinds
of decision slice 2 lets act directly.

## Error handling

| Failure | Behaviour |
|---|---|
| vLLM unreachable or errors | Unchanged: the exception propagates, the entry is not ACKed, and it is redelivered. |
| Model output unparseable or invalid | Recorded as an **invalid** proposal with `raw` and `problem`; logged at WARNING; counted. |
| Output truncated at `max_tokens` | Unparseable, so **invalid**. |
| Live state unavailable | House reads `Live home state unavailable.`; Now still renders time and sun if it can, and omits people otherwise. |
| Entity without a live-state entry, name or room | Entity ID, no room, listed under "Other". |
| Timezone unreadable | `get_user_timezone` falls back to env, then UTC. |
| Publishing the proposal fails | Logged, and the entry is still ACKed. This is the same isolation as passive observations, so a Redis `maxmemory` rejection cannot loop inference. |
| Counter `HINCRBY` fails | Logged and ignored. |

## Rollout

1. Merge one alfred PR. Merging deploys it; the merge is the owner's call, as for every
   alfred merge.
2. alfred-home-service#25 ships whenever it is ready. Rooms appear in the House section
   on its deploy, with no Alfred change.
3. Run the read-only checks in Measurement and post them to #285.
4. After a week, post the shadow report to #285 for the owner's verdicts.

**Rollback:** revert the PR. `proposal` is optional, and the decision counters expire on
their own.

## Testing

TDD, unit level. `prompt.py` is pure, so most cases are table tests.

- **House:**
  - grouping by `attributes.area`, with rooms sorted and "Other" last;
  - an entity with no area, no `friendly_name`, or a domain outside `HOUSE_DOMAINS`;
  - each per-domain detail (light brightness percentage, media title, climate
    temperatures);
  - live state unavailable.
- **Now:**
  - the time-of-day label at each boundary;
  - a timezone other than UTC across midnight;
  - sun up and down;
  - people present and absent.
- **What changed:** media, light, climate, person and door sensor; the allowlist drops
  `entity_picture` and `supported_features`; an entity missing from live state.
- **Size:** the production-shaped fixture (79 entities) renders under 1,000 tokens,
  checked by a character bound so the test needs no tokenizer.
- **Ordering:** the fixed and Preferences sections are byte-identical across two events,
  which is what keeps the cacheable prefix stable.
- **Parser:** every row of §3, including the legacy `{"action": "none"}`.
- **Runner:**
  - act, ask and invalid publish a proposal observation regardless of debounce;
  - none takes the debounced passive path;
  - counters are incremented;
  - `execute_action` is never called, on either path.
- **Ingestor:** an observation with a proposal produces the same memory text as one
  without.

The full gate runs once before the PR: `ruff check`, `ruff format --check`,
`mypy --strict` and `pytest`.

## Goldens for the PRD suite's `reflex` suite

These are requirements handed to the PRD eval suite's `reflex` suite (PRD slice 2), not
work in this slice. They need one new check:

| Check | Evidence | Passes when |
|---|---|---|
| `reflex_decision` | `alfred:reflex:observations` | The newest proposal for the step's event has the given `decision`, and the given `target` when one is set. |

Goldens, with `status: pending` until this slice lands:

| Golden | Steps | Expect |
|---|---|---|
| `reflex.tv_night_dims` | 22:30, people home, living-room lights on; `ha_event` Apple TV paused → playing | `reflex_decision: {decision: act or ask, target: Living Room}` |
| `reflex.tv_noon_noop` | The same at 12:30 | `reflex_decision: {decision: none}` |
| `reflex.last_out_lights_on` | One person home, then leaves; lights on | `reflex_decision: {decision: act or ask}` turning lights off |
| `reflex.one_leaves_other_home` | Two home, one leaves; lights on | `reflex_decision: {decision: none}` |
| `reflex.arrive_after_dark` | 20:00, sun down, nobody home, all off; a person arrives home | `reflex_decision: {decision: act or ask}` turning lights on |
| `reflex.arrive_daytime_noop` | The same at 13:00, sun up | `reflex_decision: {decision: none}` |
| `reflex.bedtime_lights_on` | 00:30; bedroom lamp off, living room lights on | `reflex_decision: {decision: ask}` |
| `reflex.media_pause_noop` | Mid-evening pause and resume | `reflex_decision: {decision: none}` |
| `reflex.prompt_is_compact` | Any event | `prompt_not_contains` for `button.`, `notify.`, `entity_picture`, `supported_features` |
| `reflex.latency` | Any event | `latency: {reflex_ms: 500}` |

"Nobody home → climate" waits on #286's C9 decision. Slice 2 turns the act cases into
`ha_called` checks.

## Measurement

These are read-only production checks, posted to #285.

**Before (2026-10-07):**
- prompt 14,411 tokens;
- admin overview `reflex.p50_ms` about 1,070;
- p50 1,425 ms for events within 5 minutes of another.

**After, over the first full day:**
- **Prompt tokens per Reflex call:** the `prompt_tokens` that `@track_tokens` records
  for `openai_client.infer`. The target is under ~1,000.
- **Latency:** `reflex.p50_ms` on the admin overview, under 500, and the
  event → observation p50 split by the same 5-minute rule as the Problem table.
- **Decisions:** totals per decision from `alfred:reflex:decisions:<date>`, with every
  act, ask and invalid proposal carrying a reason or a problem.

**After a week:** the shadow report, with the owner's verdicts.

## Docs touched by the implementation

- `docs/live-state.md`: the Well-known attributes section and its how-to.
- `docs/architecture.md`: Reflex's prompt and shadow decisions.
- `.claude/rules/core/reflex-engine.md`:
  - it still says Reflex "uses Ollama", but production runs vLLM through `REFLEX_BACKEND`;
  - the input is now preferences plus the house plus the event;
  - add shadow mode and the act/ask/none contract.
- `core/CLAUDE.md`: the Reflex section.
