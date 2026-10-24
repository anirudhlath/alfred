# Reflex anticipation — design

**Status:** Draft, for the owner's review
**Date:** 2026-10-24
**Issues:** PR 8 of the Pydantic AI rework ([#317](https://github.com/anirudhlath/alfred/issues/317)) is
slice 2 below. This design replaces [#286](https://github.com/anirudhlath/alfred/issues/286) (act and ask)
and [#287](https://github.com/anirudhlath/alfred/issues/287) (learning). It builds on the shadow mode of
[#285](https://github.com/anirudhlath/alfred/issues/285) and on
`docs/superpowers/specs/2026-10-07-reflex-context-design.md`. Where this document and component 12 of
`docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md` differ, this document wins.

Items marked **(my call)** were decided while writing this design, not by the owner. Each one is also
listed at the end so the owner can check it.

## Why

Reflex is Alfred's System 1. It looks at each change in the house and decides whether to do something.

**What it is for.** In the owner's words: "Anticipation is the problem we're solving." Reflex covers what
can only be anticipated and does not have to happen every time. The owner's examples:

- turning on the lights on a cloudy afternoon to make up for the light;
- putting on movie night when the owner starts watching a movie or show on any device.

**Where automations end and Reflex begins.** An automation is something the owner explicitly promotes, and
it happens every time. "The thing I don't explicitly promote to automations it should be asking or acting."

**The problem is eagerness.** "It should ask first until it learns or is not sure. … It should not be too
eager either. It's currently very eager to act." The shadow log for 2026-10-08 → 2026-10-23 (16 days)
shows this:

| | Count | Notes |
|---|---|---|
| Decisions | 2,048 | 1,680 were "none" |
| Would act | 64 | 28 of them (44%) would undo the change that had just happened |
| Would ask | 265 | about 17 a day, 57 on the worst day; 121 (46%) would undo the change that had just happened |
| Invalid | 39 | about 10% of proposals; mostly the tool `none` or a wrong target |

- Most proposals turn lights on or off (192) or pause media (52).
- 74 of the asks fall between 23:00 and 01:00.
- Spike [#297](https://github.com/anirudhlath/alfred/issues/297) tried two Pydantic AI output shapes on 83
  recorded events. With `ToolOutput` the model acted or asked on 30 of them, which is far too eager. With
  `NativeOutput` it never acted at all.
- A rule in the prompt telling the model not to undo changes was tried in #296. It was dropped because it
  broke a control golden.

**Reflex cannot tell who changed something.** `StateChangedEvent` carries no cause. Home-service drops the
`context` that Home Assistant attaches to every state change. So Reflex cannot tell a person turning off a
lamp from its own action, an automation, or a wall switch. The owner wants this anyway, for oversight: "I
think it's important to identify actions or events invoked by the user vs alfred. This will also help with
oversight on who is doing what."

## The ladder

Every situation lands on exactly one rung. Most land on the last.

| Rung | When | What happens |
|---|---|---|
| **Automation** | Only what the owner explicitly promoted | Runs every time, as the owner's own trigger |
| **Reflex acts** | Learned, confident right now, and an everyday action | Acts, tells the owner what it did and offers an undo |
| **Reflex asks** | New or uncertain | Asks by notification; the question expires |
| **Nothing** | Everything else | Nothing, and nothing is sent |

## Goals

1. Every state change carries its cause: a person, Alfred (and which part of Alfred), a Home Assistant
   automation, or something outside Home Assistant. The owner can see who did what.
2. Reflex never reverses a change a person just made.
3. When unsure, Reflex asks by notification. The question expires, and answering works when the PWA is
   closed.
4. Reflex learns from answers. A yes makes it more confident in that situation, and a no stops the
   question from coming back.
5. Once confident, Reflex acts alone on everyday things. It says what it did and offers an undo.
6. Reflex can suggest turning a pattern into an automation. Only the owner promotes.
7. Reflex's choices are measured by evals built from the shadow log, and different models are compared on
   them.

## Anti-goals

Any of these is a failure even if everything else works:

- **Too eager:** Reflex acts or asks where the owner would have wanted nothing.
- **Nagging:** a declined question comes back; questions arrive late at night; more than a handful a day.
- **Reversing a person:** Reflex turns back something a person just changed.
- **Silent changes:** Reflex changes something without saying so, or without its change being labelled
  as Reflex's.
- **Self-promotion:** anything becomes an automation without the owner's yes.
- **Learning from itself:** Reflex counts its own or Alfred's actions as the owner's habits.

## Non-goals

- **PWA web push.** It is unfinished (PWA phase 4), and the owner will finish it later: "let's just make
  sure there is a way it can ask me through a notification." Asks use the channels that exist today. The
  ask path must not depend on the channel, so push can be added later.
- **Inline accept and reject buttons on notifications** ([#64](https://github.com/anirudhlath/alfred/issues/64)).
  A reply or the Door is enough.
- **Reflex touching non-benign tools, except the scripts the owner allows.** Reflex's audience stays
  benign tools, plus HA scripts on the terms in slice 2. The router keeps rejecting anything else from
  Reflex as an `autonomy_violation`. Locks and the alarm always go through the confirmation flow, whoever
  asks.
- **System 2 and triggers asking before they act** ([#388](https://github.com/anirudhlath/alfred/issues/388)).
  This design is the Reflex half of that principle. #388 stays for the rest.
- **A preference model designed up front.** Each slice ships, runs on real data, and is measured before
  the next one starts.

## Constraints

- **Latency.** Event to decision stays under 500 ms (`.claude/rules/core/reflex-engine.md`). Inference has
  no side effects, and Reflex never calls System 2.
- **The owner's confirmation rule (2026-10-19), unchanged:**
  - the owner's requests run, except unsafe ones, which ask first;
  - Alfred's own actions ask unless promoted;
  - a trigger the owner created counts as the owner's;
  - any "sir" speaker can approve by reply;
  - a guest never approves.
- **The repo is public.** The shadow log names people and devices and quotes media titles. Goldens built
  from it are rewritten with generic names before they enter the repo. Shadow reports stay private.
- **The local vLLM is shared with production.** Evals run one request at a time. Swapping the served model
  for a comparison needs the owner's go.
- **Every merge to master deploys, and needs the owner's go.**
- **Two repos.** Slice 1 changes alfred-home-service as well as Alfred.

## The slices

Each slice ships, runs in production, and is measured before the next starts. The order is the owner's.

### Slice 1: who did it

**What exists.** Home Assistant attaches a `context` to every state change: an `id`, the `user_id` of the
HA user who caused it (if any), and a `parent_id` when an automation or script caused it.

- `app/ha_connection.py` in home-service passes on only the event's `data`, so the context is dropped.
- `StateChangedEvent` (`bus/schemas/events.py`, mirrored in `sdk/alfred_sdk/events.py`) has no field for
  it. Home-service builds the event with the SDK's class, and its SDK is pinned to an Alfred commit. So
  the Alfred change merges (and deploys) first, and home-service then moves its pin and sends the field.
- When Alfred calls a service, home-service discards HA's reply, which carries the context of the call.
  It returns only the domain, service, entities, data and status.
- Alfred's `ActionRequest.source` already names the part of Alfred that asked: `reflex-engine`,
  `conscious-engine`, `trigger-engine` or `admin-api`. It never reaches home-service.

**What changes.**

1. **Home-service forwards the context** of each state change: `id`, `user_id` and `parent_id`.
   `StateChangedEvent` and its SDK mirror gain an optional `context`. Events from an older home-service
   have none.
2. **Home-service returns the context id of each service call** in its action result. Alfred records
   which request and which part of Alfred it belonged to, with that id, in a log of its calls kept 30
   days, which the oversight report joins to the state changes they caused (**my call**; updated while
   planning, from a 24-hour key, so the report can look back as far as the other logs).
3. **Alfred's changes are recognised by HA user.** Home-service must connect to HA as an HA user of its
   own, so every change it makes carries that user's id. If it connects with the owner's token today,
   Alfred's changes look like the owner's. The builder checks which user it is. Creating a dedicated HA
   user is an HA admin step and needs the owner's go when the slice is built.
4. **The cause.** Each state change gets one of four causes:

   | Cause | Rule |
   |---|---|
   | `person` | `user_id` is set and is not Alfred's HA user. The HA user is mapped to an Alfred person where a mapping exists. Otherwise it is "a person". |
   | `alfred` | `user_id` is Alfred's HA user. The part (Reflex, System 2, a trigger, the admin API) comes from the context-id join, once the call's result has arrived. |
   | `automation` | No `user_id`, `parent_id` set: an HA automation, script or scene. |
   | `outside` | Neither is set: a wall switch, a device's own app or schedule, a voice assistant, or HA itself. |

   The state change can reach Alfred before the call's result does. The cause `alfred` needs only the
   `user_id`, so it is known at once. The part is filled in by the join, which is why reports read it
   later rather than at the moment of the change.
5. **The cause travels everywhere the change goes:** into Reflex's prompt, into the shadow log and into
   episodic memory. Alfred must never learn its own actions as the owner's habits. Today the Librarian
   reads no state changes at all (it consolidates scratchpad notes), so the rule that habit detection
   counts only `person` changes applies when slice 3 builds that path (updated while planning).
6. **Oversight.** A report, run like the shadow report, lists the changes in a window by cause, and lists
   everything Alfred changed with the part that did it and the reason it gave (**my call**: a report
   first; asking Alfred "who turned on the kitchen light?" can come later if the owner wants it).

7. **Reflex records what it saw.** Today the shadow log keeps the triggering event and the proposal, but
   not the rendered prompt, and "none" decisions only as debounced passive observations. From this slice
   on, every decision that reaches the model is recorded privately with its rendered prompt and the
   cause, in a capped store kept 30 days (**my call**). This is the data slice 2's replay set and model
   comparison need, so it starts collecting before slice 2 is built.
8. **The evals' fake Home Assistant sends a context** on each state change, so goldens can say who caused
   it. Without one, every change in an eval would read as `outside`.

**For the never-reverse rule, `outside` counts as a person** (**my call**). A wall switch is a person, and
Reflex should leave alone anything it cannot attribute.

**Done when** a week of production state changes shows a cause on every event, and the owner's own
changes, Alfred's, automations' and wall-switch changes each come out right in a spot check.

### Slice 2: Reflex asks, and acts on nothing yet

This slice is PR 8 of #317.

**Never reverse a person.** This is a rule in code, applied after the model answers, not a prompt rule.

- **Reversing means moving an entity back toward the state it had before a person changed it** (cause
  `person` or `outside`) in the last 60 minutes (**my call** on the window; it is a setting):
  - turning off what a person turned on, or on what they turned off;
  - pausing what they started, or resuming what they paused;
  - changing a brightness or volume a person just set.
- A proposal that would reverse any entity becomes "none", recorded with the reason `would_reverse`. A
  target that names a room or area is expanded to its entities first.
- Anything else stays allowed. Dimming a lamp a person just turned on, because a movie started, is not a
  reversal. In slice 2 it can only be asked about anyway.
- The prompt also lists the entities a person just changed, so the model has less reason to propose them.
  The filter is what guarantees the rule.
- Existing goldens that change a light and then expect Reflex to act on it, such as `arrive_after_dark`,
  are given an Alfred or automation cause, or rewritten, so they test what they meant to.

**Asks are notifications that expire.** An ask is a parked action from Reflex, held in the same pending
store and resolved the same ways as a critical action, with these differences:

- **Expiry:** 10 minutes (**my call**, a setting). An anticipation is about now. Critical actions keep 300 s.
- **Answers are recorded.** Today a denied parked action is only logged and an expired one disappears. For
  Reflex asks, yes, no and expired are each recorded on a Reflex answers stream, which slice 3 learns
  from.
- **Channels:**
  - **Signal:** text that says what Reflex noticed, what it would do and why, and "reply yes or no";
  - **the PWA** when it is open, through the Door;
  - **the native iOS app** by APNs, as an alert.

  Urgency is IMPORTANT: no speech and no satellites.
- **Answering by reply.** A reply on the channel the ask went to answers the newest open Reflex ask, using
  the existing approval-reply classifier. Today replies answer only parked System 2 runs, so this is new.
  An open parked run in that conversation takes precedence. A reply the classifier finds unclear is
  handled as an ordinary message. Who may answer is unchanged: any "sir" speaker, never a guest.
- **A yes executes** through today's path: a confirmed republish, the router, then home-service. Reflex
  sends a notification only if the action fails (**my call**; a "Done" for every yes is noise).
- **Not nagging** (**my calls**, all settings):
  - one open ask at a time;
  - the same tool and target is not asked again within 2 hours;
  - at most 6 asks a day;
  - no asks during quiet hours, a setting that defaults to 23:00–07:00 in the owner's time zone;
  - no asks while do-not-disturb is on, since DND would delay an expiring question until it is stale.
    Today DND is only a manual switch or a calendar meeting, and Reflex's checker has no calendar, so
    quiet hours are what keep late nights quiet.

  An ask held back by these limits is recorded in the shadow log as "would have asked".
- **Fired triggers too.** Reflex also decides on `TriggerFired` events. Those decisions follow the same
  modes, limits and answers as state changes, and are part of the replay set (**my call**).
- **Modes.** A setting, `shadow`, `ask` or `act` (**my call** on the name), is the kill switch. It ships
  as `shadow`, and the owner switches it to `ask`. Per-tool enabling from #286 stays: an ask goes out only
  for tools turned on. In `ask` mode, an "act" from the model goes out as an ask.

**Reflex moves to Pydantic AI** (component 12 of the adoption design), with Ollama removed and the prompt
in the prompt store. One difference: the output shape is chosen by the eval comparison below, not fixed in
advance. That means `TextOutput` with today's parser, `ToolOutput`, `NativeOutput`, or a plain JSON schema
(**my call**; #297 showed the shape changes how eager the model is).

**Scripts the owner allows.** In this house every mood, movie night included, is an HA script, and
there are no scenes. Scripts are rated `elevated`, because a script can do anything, and Reflex's tools
leave them out. The owner decided (2026-10-24): Reflex may run the scripts the owner allows, and "once I
allow it, it should whitelist that script."

- Reflex may propose running any HA script, but only as an ask, whatever the mode.
- The owner's yes runs it and adds the script to Reflex's allowed list. From then on it is treated like
  any benign tool: asked about, learned, and in slice 3 acted on alone once learned and turned on for
  acting.
- A no, an expiry or a guest's reply allows nothing.
- The ask says plainly that it would run a script, and names it.
- The list is a setting the owner can edit, including removing a script.
- Other elevated domains (automations, climate, covers, vacuums, water heaters) stay off-limits to Reflex
  (**my call**; the owner asked only about scripts).
- The router lets a Reflex script action through when it is confirmed by an ask, or when the script is on
  the list. It rejects every other non-benign Reflex action as today.

**Reflex sees the weather and the sun.** Today neither is in the attention set, and the prompt carries
no weather, so a cloudy afternoon is not an event Reflex ever sees. This slice adds the `weather` and
`sun` entities to the attention set and the weather condition to the prompt's "Now" line (**my call**).
Both change only a few times a day.

**Evals built from the shadow log.**

- **A replay set.** The decisions recorded since slice 1, each with its rendered prompt and cause, are
  labelled with what should have happened. Most labels are "none". The first pass applies the rules of
  this design: anything reversing a person's change, a repeat, or a late-night ask is "none". The owner
  reviews the short list labelled "ask" privately (**my call**). At least two weeks of recordings come
  before the model comparison (**my call**). The 16 days recorded before slice 1 have events and
  proposals but no prompt or cause, so they inform the labels and goldens but cannot be replayed as they
  were. The set stays out of the repo. Cases that go into the repo as goldens are rewritten with generic
  names.
- **New goldens:**
  - a person turns a light off, and nothing touches it;
  - a script not on the allowed list is only ever asked about;
  - the same change made by Alfred is not treated as a person's;
  - a movie starting on any player leads to an ask about the room's lights;
  - nothing is asked during DND;
  - an `outside` change counts as a person's.
- **Comparing models.** Each candidate runs the reflex suite and the replay set. Each is scored on wrong
  asks, missed asks, `would_reverse` (must be 0), invalid replies, and latency p50 and p95.
  - Today evals use one model for every role and force `vllm:`, and the Reflex client sends no
    Authorization header. Comparing hosted models through OpenRouter needs a per-role model setting in the
    evals. The header comes free with Pydantic AI.
  - Only the generic goldens go to a hosted model. Production Reflex stays on the local vLLM unless the
    comparison favours another model and the owner decides to switch.

**Done when** the replay set and goldens pass on the chosen model, the owner has switched to `ask`, and a
week of production meets slice 2's metrics below.

### Slice 3: learning

**What Reflex remembers.** Each answer is recorded with its situation:

- what changed (domain and transition);
- the room;
- part of the day and weekday or weekend;
- who is home;
- the sun;
- the weather condition (the `weather` entity's state, for example cloudy).

Live state carries no cloud cover percentage or outdoor light level today, so the condition is what
Reflex has. The records are structured, not prose in `learned.md`. Reflex finds "habits that apply" by
matching the situation (**my call**: exact matching first; #287's embedding retrieval only if matching
proves too narrow). The latency budget holds.

**How answers count** (**my calls**, all settings): each situation-and-action pair keeps a score.

| Answer | Effect |
|---|---|
| Yes | +1 |
| No | −1; no asks for that pair for 30 days, 90 after a second no |
| Expired | −0.25: a reply not given counts a little as a no |
| Undo of a Reflex action, or a person reversing it within 10 minutes | −2, and back to asking |

**Acting alone.** The confirmation rule says Alfred's own actions ask unless promoted. For Reflex the
owner refined it while this design was brainstormed: "It should ask first until it learns or is not
sure", and what is not promoted "it should be asking or acting". So a learned habit may act, and
promotion is for what should happen every time. Switching to `act` mode and turning tools on for acting
are the owner's choices. In `act` mode, Reflex acts without asking when all of these hold:

- the pair has at least 3 yeses and no "no" or undo in the last 30 days (**my call**);
- the tool is benign, or a script on the allowed list, and the owner has turned it on for acting. The
  owner's everyday set is lights, media players and the allowed mood scripts. It is a setting, not a
  list in code: the repo hardcodes no tool lists.

When it acts, it says what it did and offers an undo for 10 minutes, on the same channels as an ask.
Undo restores the recorded state of each entity it touched. Slice 1's causes make the "person reversed
it" case visible.

**Learning from the owner's own actions.** Habit detection over state changes, which counts only `person` changes
(the Librarian reads no state changes today, so this slice builds that path), proposes habits. Reflex may then ask about one: "You usually dim the
living room when a movie starts. Want me to do that?" A one-off change never becomes a habit. The owner:
"Something temporary shouldn't."

- The Librarian's limits today (3 occurrences in 7 days for a routine) are instructions in an LLM prompt,
  not counts the code enforces.
- So before Reflex asks about a habit drawn from the owner's actions, code checks that a person did it
  at least 3 times in matching situations within 14 days (**my call**).

**Reflex sees what it learned without a restart.** Today `ReflexEngine` caches preferences for the life
of the process, so a new preference reaches Reflex only after a restart. That cache must refresh.

### Slice 4: promotion

**Reflex suggests, and the owner decides.**

- A habit Reflex has acted on alone 5 times over at least 2 weeks with no undo gets one suggestion: "Want
  me to make this an automation, so it happens every time?" (**my call** on the bar).
- The suggestion uses the ask path with a 24-hour expiry. It is not urgent.
- **Yes** creates a trigger owned by the owner, marked as promoted from Reflex, through the triggers
  process and `TriggerStore` like any other trigger. It counts as the owner's under the confirmation
  rule, and Reflex stops handling that habit.
- **No** means it is not suggested again for 90 days.
- The Librarian's routine suggestion today is an INFORMATIONAL notification with no way to answer, and no
  code ever makes a routine active. It is folded into this path, so there is one way to suggest an
  automation (**my call**).

**First, triggers must run their home actions.** A trigger with an action publishes an unconfirmed
`ActionRequest` to `alfred:actions`. Actions aimed at Alfred's own processes run, such as the
deferred-notification drain. Actions aimed at home-service never do: the System 2 process routes only
confirmed domain actions, and the triggers process handles only its own internal ones. One enabled
trigger in production carries a home action today. This is fixed before slice 4, or sooner as its own
bug.

## Measuring

The shadow log stays the yardstick. Its report gains the cause, the asks with their answers, and the
actions with their undos. These numbers are starting points. The owner: "Not sure, we'll see." They get
revised once real data comes in.

| Metric | Baseline (16-day shadow log) | Target |
|---|---|---|
| Proposals that would reverse a person's change | ~45% | 0 |
| Questions a day | ~17 | a handful (at most 6, the cap) |
| Questions answered yes | unknown | most of them |
| Invalid replies | ~10% of proposals | near zero |
| Reflex actions undone | none yet (slice 3) | rare |
| Event to decision, p50 | under 500 ms target | under 500 ms |

## What #286 and #287 become

Both are replaced by this design. Their acceptance criteria are carried as follows:

- **#286:**
  - act with undo → slice 3;
  - ask with a one-tap suggestion → slice 2 (a reply or the Door);
  - per-kind enabling, the kill switch and the cooldown → slice 2.
- **#287:**
  - answers stored with their situation → slices 2 and 3;
  - "habits that apply" → slice 3;
  - yes acts next time → slice 3, after 3 yeses rather than one;
  - no stops it → slice 3;
  - p50 under 500 ms → kept.

Closing or rewriting those issues is a public post. The builder asks before doing it.

## Left to the builder

- Names of fields, streams, keys and settings, and the storage format for answers and habits.
- How an HA user maps to an Alfred person.
- Notification wording, within the rules above.
- The exact situation keys in slice 3.
- The agent's output shape, chosen by the eval comparison.
- Which hosted models to compare, within a cost the owner agrees to before the runs.
- How scripts reach Reflex's tool list and prompt, within the prompt budget.

## Calls made in this design (for the owner to check)

1. Alfred's calls are logged with their context ids for 30 days, and the report joins them to the changes (updated while planning; was a 24-hour key).
2. `outside` changes count as a person's for the never-reverse rule.
3. Oversight starts as a report. Asking Alfred "who did this?" comes later if wanted.
4. Every decision is recorded privately with its prompt and cause for 30 days, starting in slice 1.
5. Reversing means moving an entity back toward its state before a person's change, within 60 minutes.
   Other changes to that entity stay allowed.
6. Asks expire after 10 minutes. A yes is confirmed only on failure.
7. Limits: one open ask, no repeat within 2 hours, 6 asks a day, none in quiet hours (23:00–07:00 by
   default) or during DND.
8. Fired-trigger decisions follow the same rules as state changes. Of the elevated domains, only scripts
   open up to Reflex.
9. Reflex watches the weather and sun entities, and the weather condition joins the prompt.
10. A `shadow` / `ask` / `act` mode setting is the kill switch, and it ships as `shadow`.
11. The output shape is chosen by evals, after at least two weeks of recordings.
12. The owner privately reviews the replay set's "ask" labels.
13. Habits use structured situation matching before embeddings.
14. A habit drawn from the owner's actions needs 3 occurrences in 14 days, counted in code.
15. Scoring: yes +1, no −1 (30 or 90 days of silence), expired −0.25, undo −2. Acting alone needs 3 yeses
    and no "no" in 30 days.
16. Promotion is suggested after 5 actions with no undo over 2 weeks. The suggestion expires in 24 hours,
    and a no means not again for 90 days.
17. The Librarian's routine suggestion is folded into Reflex's promotion path.
