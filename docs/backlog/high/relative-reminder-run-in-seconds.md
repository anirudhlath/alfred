# Relative reminders: `run_in_seconds` on create_trigger (stop LLM clock arithmetic)

## Summary

"Remind me in 5 seconds" scheduled 2 minutes late in live use (2026-07-18): the prompt clock
read 01:57:56, the LLM computed `run_at = 02:00:01` instead of 01:58:01 — an arithmetic slip.
The trigger engine then fired at the requested time within 1.9ms. Scheduling precision is now
millisecond-grade; the residual error source is the LLM computing absolute timestamps from the
prompt's Current Time for *relative* requests.

## Context / Motivation

- The instant-triggers work (PR #27) made firing exact; relative-time requests are the flagship
  reminder flow and currently depend on in-context timestamp arithmetic by the model.
- Absolute requests ("at 3pm") are fine — the model reads the wall clock and emits an offset
  timestamp, no arithmetic involved.

## Proposed fix

- Add `run_in_seconds: float | None` to `TimeTrigger.Conditions` (mutually exclusive with
  `run_at`/`cron`). Resolve it server-side at the tool boundary (`normalize_conditions` or
  `create_trigger`): `run_at = now_utc + run_in_seconds`, stored aware as usual — the model
  never does math.
- Update the personality prompt + tool docs: relative requests → `run_in_seconds`; absolute
  wall-clock requests → `run_at` with offset.

## Acceptance criteria

- LLM tool call `{"run_in_seconds": 5}` produces a stored aware `run_at` ≈ now+5s and fires
  within the scheduler's normal precision.
- `run_at`/`cron` behavior unchanged; supplying two of the three fields is a validation error.
- Prompt instructs the model to prefer `run_in_seconds` for "in N seconds/minutes/hours".
- Live check: "remind me in 5 seconds" lands within ~1s of +5s (LLM response latency aside).
