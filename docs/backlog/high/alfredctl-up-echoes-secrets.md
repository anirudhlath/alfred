# `alfredctl up` prints every secret it passes to the container

**Priority:** high
**Source:** review of the `EMBEDDING_BACKEND` branch, 2026-09-04 (forward note #4)

## Summary

`_run` echoes the command it is about to execute, and for `up` that command carries
`-e KEY=value` for the entire merged environment. Every secret in `.env` — the
OpenRouter key, the HA long-lived token, basic-auth credentials embedded in a host, and
the secrets passphrase — is printed to the terminal, into scrollback, and into whatever
CI log or pasted transcript the operator produces next. `alfredctl doctor` now redacts
credentials out of its own output; `up` prints the same values in full a few lines
later, which is the gap this ticket closes.

## Context / Motivation

- `alfredctl/main.py:35` — `_run` does `console.print(f"[dim]$ {' '.join(cmd)}[/dim]")`
  before `subprocess.run`. Useful for reproducing a run by hand; indiscriminate about
  what it is quoting.
- `alfredctl/launch.py:54-57` — `_env_pairs` emits `["-e", f"{key}={value}"]` for the
  whole merged dict: everything in `.env`, plus `ALFRED_SECRETS_PASSPHRASE` (`:48`),
  `HF_TOKEN` (`:49-50`), and any `-e KEY=value` the operator passed. Those pairs are
  `plan.run_args`, which `main.up` hands straight to `_run`.
- Verified 2026-09-04 by building a plan from a `.env` holding four marked secrets and
  inspecting the line `_run` would print: `OPENROUTER_API_KEY`, `HA_TOKEN`, the
  `EMBEDDING_HOST` basic-auth password and the passphrase all appear verbatim.
- The same `hunter2` that `alfredctl/doctor.py` (`_redact_userinfo`, added on the
  `EMBEDDING_BACKEND` branch) now prints as `***@host` is printed in full by `up`. The
  redaction is real but partial: it covers the checker, not the launcher.
- Scope note: only `up` passes `-e` pairs. `build` and `smoke` echo through the same
  `_run`, so a fix belongs in `_run` rather than at one call site.

## Acceptance Criteria

- [ ] `_run` redacts before printing: `-e KEY=value` becomes `-e KEY=***` (or the value
      is elided) for every key, rather than a maintained list of "secret-looking" names
      that a new variable silently escapes.
- [ ] Any URL in the echoed command has its userinfo redacted, matching what
      `doctor._redact_userinfo` does — shared helper rather than a second copy.
- [ ] The printed line stays useful for reproducing a run: the flags, image and
      container name are unchanged, and the redaction is visible as redaction.
- [ ] A test asserts a known secret in `.env` never reaches the echoed command, driven
      through `build_plan` + `_run` so it covers the join, not just the formatting.

## Notes

- Pre-existing; not introduced by the embedding-backend work, which is why it was
  ticketed rather than folded into that branch.
- Consider whether the echo belongs behind `--verbose` at all, given the failure mode is
  a screenshot or a pasted CI log rather than a live terminal.
- Per `docs/backlog/README.md` non-sensitive work belongs in a GitHub Issue; this file
  was written by an agent with no GitHub auth, so mirror it when convenient. It is not
  marked 🔒: the leak is of the operator's own secrets to their own terminal, with no
  live exposure recorded here.
