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
  still known. A change by a person, or by a switch, an app or a device nobody can name, becomes
  that entity's record. A change by Alfred or an automation deletes it: that is the latest word
  on the entity. An attributes-only report with the same context id as the record (a bulb
  giving its brightness after being turned on) leaves it as it is, and an event older than the
  record never overwrites or clears it.
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
- **One outcome per decision.** Every decision ends in a `ReflexOutcome`: `none`, `invalid`,
  `would_reverse` (the proposal would undo a person's change; it names the entities and is not
  published as a proposal), `shadow` (published; nothing executes) and, for later slices,
  `asked` and `held`. It is kept on the decision-log record and on the observation, counted in
  `alfred:reflex:decisions:<UTC date>` as `outcome:<kind>` (and `<kind>:<reason>` when there is
  a reason), and printed as the Outcomes table of `python -m core.reflex.shadow_report`.
- **Fail closed.** A Redis error while reading the settings or the records propagates, before
  the model is asked: the entry stays pending and is retried rather than decided without
  knowing what a person did. Noting a change is best-effort and never raises.

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
