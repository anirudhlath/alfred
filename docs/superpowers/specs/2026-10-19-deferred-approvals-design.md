# Deferred approvals for critical actions — design

Part of the Pydantic AI rework ([#317](https://github.com/anirudhlath/alfred/issues/317)), PR 5. It refines
component 8 ("Confirmations and follow-up delivery") of
`docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md` against the code as it stands after the
tool-search PR. Where the two differ, this document wins. Closes
[#306](https://github.com/anirudhlath/alfred/issues/306) and [#307](https://github.com/anirudhlath/alfred/issues/307).

## Why

The owner's rule (2026-10-19): the owner's own requests run, except unsafe or dangerous ones, which ask
first. Alfred's own actions ask unless the owner has promoted them; that half is
[#388](https://github.com/anirudhlath/alfred/issues/388) and comes after this rework. This PR delivers the first half, the PRD row "Confirmation required for critical
actions (locks, alarm, garage) — even when you asked".

Today's flow has four faults:

- **The model can approve its own action.** A critical call returns `confirmation_required:<id>` as tool
  text. `confirm_pending_action` is offered in the same turn, and only its docstring stops the model from
  calling it. The tool-search evals caught it doing exactly that once (a switch was turned on).
- **The owner never hears the outcome.** `_consume_internal_actions` (`core/conscious/__main__.py`) runs
  a confirmed action and logs the result at INFO, even on error. The PWA's "applied" state waits on
  `home_action_results`, which nothing writes.
- **There is no way to say no**, except letting the approval expire.
- **Late replies are lost.** The web channel and the satellites stop waiting after 60 s and drop
  whatever System 2 says afterwards. Only Signal delivers every response.

## Goals

1. A critical tool call that System 2 makes for the owner pauses, and runs only after the owner approves it.
2. Only the owner can approve or deny: with the PWA's buttons or with a reply in the conversation. The
   model never sees an approval id and has no tool that approves anything.
3. The owner is told how an approved action went, success or failure, on the channel the request came
   from, and the outcome is in the session history.
4. A reply that arrives after the channel stopped waiting still reaches the owner.

## Anti-goals

Any of these is a failure even if everything else works:

- an action runs without the owner's yes, or runs twice;
- an approval runs an action long after it was asked for: the window stays 300 s;
- a guest's reply approves anything;
- an unanswered approval breaks the conversation that follows it.

## Non-goals

- Alfred-initiated actions (Reflex, triggers Alfred creates, proactive suggestions): #388.
- Which tools count as critical: services declare `risk` in their manifests, unchanged.
- Approve buttons on lock-screen notifications (#64).

## Decisions

### 1. The gate

- `RegistryToolset`'s argument validator (`validate` in `core/conscious/tools/registry.py`) raises
  `ApprovalRequired` for a tool whose `risk` is `critical`, unless `ctx.tool_call_approved`. The validator
  re-runs when the run resumes, which is why it checks the flag. The exception's metadata carries the
  `reason` argument, the registry tool name, the target service and the arguments.
- The gate stays in the validator and not in `call_tool`. A deferral raised there happens before
  `RecordEffects` and `CatalogToolset.call_tool`, so a parked call is neither recorded as failed nor
  listed in `actions_taken`.
- `call_tool` sets `ActionRequest.confirmed = ctx.tool_call_approved`. The router's `_intercept_critical`
  stays for critical actions from other sources and as defence in depth.
- The System 2 agent's output type becomes `[str, DeferredToolRequests]`; with `str` alone a deferral
  raises `UserError`.
- `confirm_pending_action` is removed, with its prompt mentions, docs and the `confirmation_required:`
  handling in `RegistryToolset` and `RecordEffects`.

### 2. Parking

A run that ends in `DeferredToolRequests` is parked by the engine:

- **One approval per run.** A response can defer more than one call, and resuming needs a decision for
  every one of them. So the parked run gets one approval id, and approving or denying applies to all its
  deferred calls. *(My call: the evals saw exactly one critical call per turn.)*
- **The id is derived from the request's `event_id`**, and the record is written with `SET NX`, so a
  redelivered turn cannot park twice or notify twice.
- **The record** `alfred:pending:{approval_id}`, TTL 300 s, holds `kind="parked_run"`, the session id,
  channel, identity, request `event_id`, creation time, the deferred calls (tool call id, registry name,
  target service, arguments, reason) and the run's messages.
- **The session** stores the run's messages as usual, open call included, plus a `pending_approval`
  field naming the id. Pydantic AI 2.55 refuses a new prompt on a history whose final response has an
  unanswered call (`UserError`). It closes the call itself only when the history ends in a request, as it
  does when a benign sibling's return follows the response. So the engine closes the open call
  (`repair_messages`, which marks the return it synthesizes) on the next turn that does not resume. A turn
  that resumes passes the history untouched, because its open calls are the ones being answered.
- **The ledger** gets a `parked` entry per deferred call, so a redelivery answers "I've asked for your
  confirmation" from the ledger instead of re-running the turn.
- **The notification** keeps today's title ("Confirmation required"), URGENT urgency and metadata keys
  (`pending_action_id`, `tool_name`, `parameters`, `reason`), so the PWA's Door works unchanged. Its source
  becomes `conscious-engine`.
- **The reply** is a fixed template built from the calls' `reason`s, ending in "Shall I, sir?".

Router-intercepted actions (`kind="action"`) live in the same store under the same key prefix. `GET
/api/actions/pending` and `GET /api/actions/{id}` project both kinds into today's payload shape. For a run
with several calls, the payload shows the first call, and its `reason` names them all.

### 3. Resolution

| source | path |
|---|---|
| PWA Confirm / Deny | `POST /api/actions/{id}/confirm` (exists) and a new `POST /api/actions/{id}/deny`, both `require_authenticated`. The endpoint claims the record with `GETDEL`. A `parked_run` publishes an `ApprovalDecision` (new bus schema) to `APPROVALS_STREAM`. An `action` keeps today's republish path on confirm and is dropped on deny. The Door gains a "Don't" button. *(My call.)* |
| A reply in the conversation | The verified owner's next turn in a session with `pending_approval` first runs the approval classifier, an agent with `output_type=Literal["approve", "deny", "other"]` given the pending calls and the reply. It runs only when the turn's resolved identity is `sir`, by any method: passkey, Signal number, voice ID, or a satellite's local claim. A turn that resolves to `guest` never reaches it. The turn claims the record with `GETDEL` before resuming. *(The owner's call, 2026-10-19: they accept that while a satellite has no enrolled voiceprint, or voice ID fails, anyone in earshot can approve.)* |
| Expiry | The TTL lapses. The next turn on that session closes the open call as interrupted. No message is sent. *(My call: the Door already shows the approval as expired.)* |

Because every path claims with `GETDEL`, a Door tap and a "yes" in chat resume the run once.

The decision resumes the parked run with `deferred_tool_results`:

- **approve** → `ToolApproved`. The tool runs with `confirmed=True`, and the model writes the reply.
- **deny** → `ToolDenied("The user declined.")`, and the model acknowledges.
- **other** → `ToolDenied("Not confirmed; the user moved on.")`.

From a reply in the conversation, the reply is also the resumed run's `user_prompt`. One run takes both
(verified), so "no, but turn on the hall light" is answered in the same turn.

The classifier's model is `APPROVAL_MODEL`, defaulting to `SYSTEM2_MODEL`. Its prompt is
`approval-classifier`, kept in Langfuse with a repo default. It runs under the shared spend limits and is
traced like the other agents.

### 4. The resumed run

- **From a reply:** the resume happens inside that turn's `process_request`, under its request id. Its
  reply is that turn's reply.
- **From the PWA:** the conscious process consumes `APPROVALS_STREAM` with its own consumer group, using
  the same delivery cap and dead-letter handling as user requests. The resumed run:
  - uses the approval id as its request id, for a fresh ledger and answered marker;
  - uses the decision's time as `received_ms`, so the 300 s replay guard measures from the approval;
  - re-reads the registry tools;
  - runs as the record's identity on the record's session.
  - Its reply is an `AlfredResponse` with `followup=True` and `in_reply_to` set to the original request's
    `event_id`, delivered as in section 6.
- **Tool search:** the resumed run re-derives found tools from history, so a critical tool found by
  `search_tools` is still callable. This needs a test.
- **The session** stores the resumed run's messages and clears `pending_approval`.
- **A failed action** reaches the model as `ToolFailed`, so the reply says it failed (#306).
- **The PWA's "applied" state:** an executed approved action publishes an `ActionResult` with `request_id`
  set to the approval id to `home_action_results`, which the Door already waits on.
- **The Door shows a failure as a failure.** Today its reducer (`web/src/lib/actions.ts`) moves to
  `applied` for any matching result, and the Door shows a green "Applied" even when the status is `error`.
  It gains a failed state, and its design-system preview is updated in this PR. So when the owner
  approves on the phone, the phone shows the outcome, and the resumed reply also goes to the channel the
  request came from. *(My call: this satisfies both goal 3 and #306's "the channel they confirmed from".)*

### 5. Router-intercepted actions

Critical actions from other sources keep today's path. To close #306 for them too,
`_consume_internal_actions` notifies the outcome through the notification dispatcher, logs a failure at
WARNING ([#254](https://github.com/anirudhlath/alfred/issues/254) item 6), and publishes the same
`ActionResult`.

### 6. Follow-up delivery (#307)

- `AlfredResponse` gains `followup: bool = False`. `in_reply_to` already exists.
- When `publish_and_wait` times out, it registers `alfred:followup:{request_id}` (TTL 10 min). Its fallback
  text becomes "This is taking a little longer, sir — I'll reply as soon as it's ready."
- The channels process runs one consumer on `USER_RESPONSES_STREAM`. It delivers a response only when
  `followup` is set or its `in_reply_to` is registered, so a reply someone is still waiting for is never
  shown twice.
  - **Web:** to the session's socket if it is connected, otherwise through the notification dispatcher.
  - **Satellite:** spoken on the satellite named in the session.
  - **Signal:** unchanged, since the bridge already sends every Signal response.
- A turn is never cancelled because a channel stopped waiting.

### 7. Evals

`critical_actions` changes most:

- **`explicit_approval` and `natural_yes`** approve by reply ("Yes, go ahead"), which exercises the
  classifier. At least one golden approves over the confirm endpoint, as the PWA does.
- **`refusal`** also asserts that the record is gone and the action did not run.
- **`wrong_id`** is replaced by a golden in which the model tries to approve, which is impossible: the
  action stays parked, and Home Assistant is never called.
- **`repeated_approval`** checks that a double decision runs the action once.
- **The `before_consent` goldens** keep their checks, except the notification's source.

Elsewhere:

- The nine `guest_boundary` and `privacy` goldens drop their `confirm_pending_action` checks.
- The harness's pending-action reader and its `{pending_action_id}` selector read the new store.
- A new golden covers the outcome: after approval, the reply says what happened.

### 8. Docs

- `docs/autonomy.md`: approvals.
- `docs/notifications.md`: follow-ups.
- `docs/llm.md`: the agent's output, the gate, resume.
- `docs/web-frontend.md`: Deny.
- `docs/evals.md`: pending checks.
- The PRD row for critical confirmations becomes Shipped.
- `docs/qa-backlog/critical-action-confirmation-flow.md` is corrected or retired.

## Verification

- **Done (throwaway probe, Pydantic AI 2.55, `FunctionModel`):**
  - A run with one critical and one benign call runs the benign one and ends in `DeferredToolRequests`
    for the critical one.
  - The stored history survives a `ModelMessagesTypeAdapter` round trip.
  - One run with `message_history`, `user_prompt` and `deferred_tool_results` executes the approved
    call and answers the prompt.
  - With `ToolDenied`, the model sees the denial message, followed by the prompt.
  - A new prompt on the parked history without results is refused (`UserError`) when the critical call
    was the response's only call. The probe's history ended in a sibling's return, which Pydantic AI does
    tolerate. The engine therefore closes the open call with `repair_messages` on the next turn that does
    not resume, and nothing runs (`test_the_next_turn_closes_a_parked_call`).
- **Unit and engine tests:**
  - park, approve, deny and other;
  - redelivery after parking;
  - double decision;
  - expiry;
  - a guest reply;
  - search → critical call → park → resume;
  - a failed approved action;
  - follow-up delivery after a channel timeout (#307's test).
- **Evals:** branch vs master on `critical_actions`, `guest_boundary`, `privacy`, `home_control` and
  `conversation`, 5 epochs, at most 4 requests in flight.
- **After deploy:** one real approval in production with the owner, from the PWA and by reply, read back
  from Langfuse and posted to #317.

## Out of scope

- #388: Alfred-initiated actions.
- #64: notification actions.
- [#374](https://github.com/anirudhlath/alfred/issues/374): a trigger's action naming an unsearched tool.
