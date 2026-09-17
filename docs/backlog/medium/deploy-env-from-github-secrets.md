# Render the Deploy `.env` from GitHub Secrets

## Summary

The deploy `.env` at `~/code/alfred-deploy/.env` is hand-managed on lath-server. Rendering
it from repository secrets at deploy time would make rotation a GitHub action rather than
an SSH session, and would leave the box holding no long-lived plaintext credentials.

## Context / Motivation

- `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md` §11 deferred this: "A
  pre-placed file is the smallest thing that works; rotation via SSH is tolerable for now."
- The current design's safety property is that secrets never enter a workflow — the
  workflows reference `.env` only by path. Rendering from secrets **inverts** that, so it
  needs care: the file would be written by a job, and a compromised workflow could read
  every value.
- The secrets passphrase is out of scope either way: it is generated and persisted in the
  `alfred_data` volume on first boot (#158) and is not in `.env`.

## Proposed shape

- One repository secret per `.env` key, or a single `DEPLOY_ENV` secret holding the file.
- The deploy job writes it to a `0600` file in the runner's workspace and passes it to
  `alfredctl doctor --env-file` and compose's `env_file`.
- The file must not persist between runs — the runner's workspace is reused.

## Open questions

- Does writing the file from a workflow weaken the current guarantee enough to matter on a
  single-maintainer repo where the workflow and the box have the same owner?
- Rotation still requires a deploy to take effect. Is that better or worse than an SSH edit
  plus `docker compose up -d`?
