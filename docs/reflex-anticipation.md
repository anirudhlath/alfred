# Reflex anticipation

Reflex is Alfred's System 1: it looks at each change in the house and decides whether to ask
about something, act or do nothing. Anticipation is what it is for: doing, or offering, what
cannot be an automation because it does not have to happen every time (the lights on a cloudy
afternoon, movie night when a film starts on any player). The trouble so far is eagerness: in
shadow mode close to half of Reflex's proposals would undo the change that had just happened.

Design: `docs/superpowers/specs/2026-10-24-reflex-anticipation-design.md`. It replaces
[#286](https://github.com/anirudhlath/alfred/issues/286) and
[#287](https://github.com/anirudhlath/alfred/issues/287); slice 2 is PR 8 of
[#317](https://github.com/anirudhlath/alfred/issues/317). Each slice ships, runs on real data and
is measured before the next starts.

| Slice | What it adds | Doc |
|---|---|---|
| 1. Who did it | Every state change carries its cause (person, Alfred, automation, outside); Reflex's prompt shows it; every decision that reached the model is kept whole in a private log | [attribution.md](attribution.md) |
| 2. Reflex asks | Never reverse a person (a rule in code), asking instead of acting, a replay set and a model comparison built from the decision log | this page |
| 3. Learning | Answers stored with their situation; yes acts next time after three yeses, no stops it; undo | the design |
| 4. Promotion | A learned habit becomes an automation, on the owner's say-so | the design |

Nothing a proposal names is executed until the design says so: shadow mode stays.

## Settings

The owner's settings are one JSON document at `alfred:reflex:settings` (`core/reflex/settings.py`,
`ReflexSettings`), read on every decision, so a change applies to the next one. A missing,
unreadable or invalid document reads as the defaults, mode `shadow` included, and the
unusable document is warned about once per process: a typo never stops Reflex. An unknown
field is invalid, not ignored, and one bad field makes the whole document invalid. The numbers
are strict: `true` or `"60"` is a wrong type, not 1 or 60 (a whole number such as `2` is a
valid `repeat_hours`). Entries are stripped of surrounding whitespace.

| Field | Default | Bounds | What it does |
|---|---|---|---|
| `mode` | `shadow` | `shadow` or `ask` | `shadow` records decisions and asks nothing; `ask` puts a proposal that matches `ask_tools` to the owner first |
| `ask_tools` | `[]` | up to 200 non-empty strings, each at most 256 characters | fnmatch patterns over tool names: the tools Reflex may ask about. Matched against the tool registry's names, never parsed or listed in code |
| `allowed_scripts` | `[]` | up to 200 non-empty strings, each at most 256 characters | target ids the owner has approved running through an ask. Opaque: compared, never parsed |
| `reverse_window_minutes` | `60` | 1 to 1440 | how long after a person changes something Reflex will not propose moving it back ([above](#never-reverse-a-person)) |
| `ask_expiry_seconds` | `600` | 60 to 3600 | how long an ask waits for an answer before it lapses |
| `repeat_hours` | `2.0` | 0 to 168 | how long after asking about something Reflex will not ask about it again |
| `daily_ask_cap` | `6` | 0 to 50 | the most asks in one local day; `0` means none |
| `quiet_hours_start` | `23:00` | `HH:MM`, 00:00 to 23:59 | local time quiet hours begin: no ask in the window |
| `quiet_hours_end` | `07:00` | `HH:MM`, 00:00 to 23:59 | local time they end; equal to the start means no quiet hours |

The fields beyond `reverse_window_minutes` are for the ask gate, which later tasks of this
slice add: until it lands they are stored and shown, and nothing reads them.

Two ways to edit the document, both validating the whole of it before they save a byte:

- **Admin API.** `GET /api/admin/reflex/settings` returns the document, or the defaults;
  `PUT` replaces it with the body (a missing field takes its default) and answers 422, saving
  nothing, for an unknown field or a bad value. Session-gated like the other controls: see
  [admin-api.md](admin-api.md#reflex-settings).
- **Command line.** On the host that runs the container:

  ```
  docker exec alfred python -m core.reflex.settings show
  docker exec alfred python -m core.reflex.settings set mode ask
  docker exec alfred python -m core.reflex.settings set ask_tools '["<pattern>", "<tool>"]'
  docker exec alfred python -m core.reflex.settings allow-script <id>
  docker exec alfred python -m core.reflex.settings disallow-script <id>
  ```

  `set <field> <value>` reads the value as JSON when it parses (`3`, `0.5`, `'["a*"]'`) and
  as a string otherwise (`ask`, `22:30`); an invalid result is refused with exit status 1 and
  nothing is saved. `allow-script` and `disallow-script` are idempotent and keep the list's
  order. `show` prints what Reflex would read, which is the defaults when the stored document
  is unusable, and says so on stderr. The commands that change the document refuse to edit an
  unusable stored one (exit status 1, nothing saved): replace it with the admin `PUT` first. A
  missing document is edited from the defaults.

## A redelivered event is not decided again

A redelivered event is not inferred again. The runner (`core/reflex/runner.py`) checks
`alfred:reflex:decided:{event_id}` after the attention gate and before the engine; a hit logs at
INFO and the entry is ACKed without asking the model again. The marker is set (`SET NX EX`, the
`REPLAY_MARKER_TTL_SECONDS` window) only after the decision is counted and published, so a crash
before that point decides the event again, and an inference that failed or raised leaves no
marker. A `TriggerFired` uses its own `event_id`. This makes it unlikely that a redelivered
entry is inferred, counted and recorded twice; a crash between the decision and the marker can
still record an event twice, so the replay drops duplicates (below), which would otherwise
skew every count it prints.

## Never reverse a person

A rule in code, applied after the model answers: Reflex does not propose moving back what a
person just changed. It does not ask the model to remember.

- **What is kept.** The runner takes every state change of a house device (an entity whose ID
  starts with a domain in `HOUSE_DOMAINS`; a sensor update is not one) into
  `alfred:reflex:person_changes`, a hash with one field per entity (`core/reflex/reversal.py`,
  `PersonChanges.note`). It does this before the attention gate, so a change the gate drops is
  still known. A state change by a person, or by a switch, an app or a device nobody can name,
  becomes that entity's record. A change by Alfred or an automation deletes it: that is the
  latest word on the entity. An event older than the record never overwrites or clears it.
  Reading the records prunes the expired ones, each deleted only while it still holds what was
  read (a Lua compare-and-delete), so a change noted by the other consumer loop in between is
  kept.
- **Attributes only.** An event whose old and new state are the same (a brightness, a volume, a
  media position) counts only when an HA user made it: it then replaces the record, unless it
  shares the record's context id (a bulb giving its brightness after being turned on is the
  same action reporting itself). Any other attributes-only event is left alone: it neither
  writes, replaces, refreshes nor deletes. The event carries no old attributes, so a device
  report (a player's media position and metadata, a polled bulb) cannot be told apart from an
  outside app's slider, and on the live stream the reports are most of the attributes-only
  events by far. The cost: a brightness or volume set from outside HA (an app, a voice
  assistant) is not protected.
- **How long.** `reverse_window_minutes` in the settings document `alfred:reflex:settings`
  (default 60, from 1 to 1440; `core/reflex/settings.py`). The engine reads the document on
  every decision, so a change applies to the next one. A missing, unreadable or invalid
  document reads as the defaults, and a typo never stops Reflex.
- **What the model sees.** The `## Now` section ends with a `Leave alone, a person just
  changed:` line naming those entities as the House block names them (`Name (Area)`, or just
  `Name`; sorted; an entity the snapshot lacks is named by its id). It is there so the model
  does not waste a proposal; the rule below does not depend on it.
- **The rule.** `reversed_entities` takes the proposed action, those records and the live
  snapshot, and names the entities the action would move back. With no live snapshot nothing
  is reversed.
- **Which entities a proposal names.** The proposal's `target` is resolved the way home-service
  resolves it (`EntityIndex.resolve`), among the entities of the tool's domain and ignoring
  case: an entity ID, else an area (every entity of the domain in it), else a friendly name.
  The first step that finds anything decides, so a light named for its room does not stand in
  for the room, and an entity ID in another domain names nothing.
- **One outcome per decision.** Every decision ends in a `ReflexOutcome`: `none`, `invalid`,
  `would_reverse` (the proposal would undo a person's change; it names the entities and is not
  published as a proposal), `shadow` (published; nothing executes) and, for later slices,
  `asked` and `held`. It is kept on the decision-log record and on a published proposal's
  observation (a `none` or `would_reverse` is observed passively, with no outcome), counted in
  `alfred:reflex:decisions:<UTC date>` as `outcome:<kind>` (and `<kind>:<reason>` when there is
  a reason), and printed as the Outcomes table of `python -m core.reflex.shadow_report`, with
  `—` in every cell of a day written before outcomes were counted (a day with at least one
  `outcome:*` field counts the kinds it lacks as `0`). The report then lists the `would_reverse`
  decisions it reads from the decision log, newest first, at most 20 and then `(N more)`, each
  with its local time, tool, target and the entities it would have reversed. It is private,
  like the rest of the report: only counts are ever posted.
- **Fail closed.** A Redis error while reading the settings or the records propagates, before
  the model is asked: the entry stays pending and is retried rather than decided without
  knowing what a person did. Reading the records includes the prune, so a Redis error there
  fails the decision too. Noting a change is best-effort and never raises.

## The sun and the weather

Reflex attends to the sun and the weather: `sun` and `weather` are in the attention seed
(`core/reflex/attention_seed.yaml`), so a change of either is judged like a light's. The `## Now`
section carries them: its first line ends with the sun (`sun up` or `sun down`) and then the
weather condition, and the `Leave alone` line, when there is one, stays last.

```
## Now
Thu 8 Oct, 15:10 (afternoon) · sun up · partlycloudy
```

- **Which entity.** The first `weather` entity in the live state, by entity ID. A house with none
  has no condition on the line.
- **How it reads.** The entity's state with HA's hyphens turned to spaces (`clear-night` reads
  `clear night`), clipped like every other detail. An `unavailable`, `unknown` or empty state
  leaves the condition out: the first entity speaks for the house, and a later one does not
  stand in for it.
- **No sun.** With a condition but no sun, the condition follows the time with the same ` · `.
- **An entity seen before the seed named it.** The seed decides membership the first time Reflex
  sees an entity, and the `seen` set keeps that decision. A sun or weather entity Reflex saw
  before the seed listed its domain stays out until it is allowed through
  `PUT /api/admin/attention/{domain}` (see [autonomy.md](autonomy.md)); the `{domain}` there
  is the attention set's, the event's domain (`home`), not the entity's (`sun`, `weather`).

## Replay

`python -m core.reflex.replay run` decides recorded prompts again, through the same agent and
parser Reflex uses, and prints how the new decisions compare with the recorded ones. It is how a
model is compared on real traffic before the owner switches Reflex to it.

```
python -m core.reflex.replay run --since 2026-10-26T23:35:00Z [--until ISO] [--limit N] [--model MODEL] [--out FILE]
python -m core.reflex.replay run --records decisions.jsonl [--limit N] [--model MODEL] [--out FILE]
```

- **Source: exactly one of two.** `--since` reads the live decision log
  (`DecisionLog.read(since=, until=)`; `--until` goes only with `--since` and is exclusive).
  `--records FILE` reads JSONL, one `DecisionRecord.model_dump_json()` per line. Anything else is
  a usage error (exit status 2).
- **Each event once.** Records that share an event's `event_id` (a crash before the marker, or
  a log written before the marker existed) are replayed once, from the oldest; the replay
  prints how many it dropped, never an id. A record whose event has no `event_id` is kept.
- **Oldest first.** `--limit N` replays the N oldest records, counted after duplicates are
  dropped.
- **The prompt as recorded.** Each record's exact prompt is sent, with the tools that prompt
  listed (read back from it by `parse_tools`), so a record is judged on what the model saw then.
- **Model.** `--model` (for example `vllm:<served name>`) or, by default, `REFLEX_MODEL`.
- **Read-only.** The replay writes nothing to Redis, publishes nothing and starts no tracing, so
  nothing it asks reaches an observation, a counter or a span.
- **Output is counts only.** A header line (records replayed, calls that failed), a recorded by
  replayed table (act, ask, none, invalid, error), agreement, the invalid rate (replayed and
  recorded), and model latency p50 and p95. No entity id, name, prompt text or reason is ever
  printed: the prompts name the house and the people in it.
- **`--out FILE`** writes one JSONL line per record (its time, origin, recorded decision,
  replayed decision, latency in ms). It holds the owner's house, so it is created readable only
  by its owner (mode 0600) and stays off the repo.
- **Errors.** Calls that raise are counted in the `error` column, by exception class. Five in a
  row stop the run (exit status 1), naming those classes; the cause is not guessed.
- **Latency is the model call's.** The timer starts after the tools are read back from the
  prompt.

A model server is not deterministic at temperature 0: compare counts across a few hundred
records, never single answers.
