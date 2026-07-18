# Prep signal-bridge & home-assistant Repos (LICENSE, README, .gitignore, history)

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #12, #17, #43, #46, #54, #62)

> 🔒 **Sensitive:** describes a live exposure on an already-public repo — do NOT file as a public GitHub issue until remediated.

## Summary
`signal-bridge/` and `home-assistant/` ship with no LICENSE and no README, and
`signal-bridge/` additionally has no `.gitignore`. Unlike the sibling repos, these two are
currently **local-only** — they have no git remote and the `anirudhlath/alfred-signal-bridge`
and `anirudhlath/alfred-home-assistant` GitHub repos do not yet exist (`gh`: *"Could not
resolve to a Repository"*), so this is **pre-publication prep**, not post-exposure cleanup:
the goal is to make both repos release-ready before their first public push. A GitHub repo
published without a LICENSE is all-rights-reserved (nobody may legally use, copy, or modify
it) — inconsistent with the already-public siblings (`alfred` AGPL-3.0-or-later, `alfred-ios`
MIT, `home-service` MIT). The sensitivity is that `signal-bridge`'s missing `.gitignore`
means its documented `.env` workflow would sweep a **real Signal phone number** into the
first `git add .`, and `home-assistant`'s history carries dev-container runtime files that
should not ride into a fresh public repo.

## Context / Motivation
**Missing LICENSE + README (findings #12, #17, #54, #62).** Confirmed via `git ls-files`:
- `home-assistant/` tracks only 5 files — `.gitignore`, `config/automations.yaml`,
  `config/configuration.yaml`, `docker-compose.yml`, `scripts/dev-up.sh`. No LICENSE or
  README in the working tree or anywhere in history. Working-tree config is a pure
  dev/testing config (virtual template lights, MQTT publisher).
- `signal-bridge/` tracks only 6 files — `.env.example`, `Containerfile`, `app/__init__.py`,
  `app/bridge.py`, `app/signal_client.py`, `pyproject.toml`. No LICENSE, no README, and no
  `license` field in `pyproject.toml`. It is a single scaffold commit with unwired TODOs.

License decision has a dependency: `signal-bridge`'s only substantive dependency is
`alfred-sdk`, which lives inside the AGPL-3.0-or-later `alfred` monorepo. So its license is
an explicit choice — MIT to match `alfred-ios`/`home-service`, or AGPL-3.0 to match the SDK.
If MIT is chosen, deployments that combine `signal-bridge` with `alfred-sdk` are still
governed by the SDK's AGPL terms (see the `sdk-agpl-mit-conflict` finding); direct-dep
license review is otherwise out of this ticket's scope.

**signal-bridge has no `.gitignore` (finding #43).** The documented workflow is
`.env.example` → `.env` via `python-dotenv` `load_dotenv()`, so the natural next step is a
`.env` containing `SIGNAL_PHONE_NUMBER=<real phone>`. With no ignore rules, that file — plus
`.venv/`, `__pycache__/`, and any signal-cli data placed in-repo — is one `git add .` away
from publication. **Currently safe:** no `.env` exists in the tree, `.env.example` holds only
the placeholder `+1234567890`, and the single commit's history is clean (no phone numbers).
The fix is preventive: copy `home-service`'s `.gitignore` (covering `.env`, `.venv/`,
`__pycache__/`, `*.egg-info/`) before publishing.

**home-assistant history carries benign runtime files (finding #46).** Commit `27d4e12`
accidentally committed ~450 lines of HA runtime files —
`config/home-assistant.log`, `config/home-assistant.log.1`, `config/.HA_VERSION`,
`config/.ha_run.lock`, `config/blueprints/` — and commit `5da91fc` removed them and expanded
`.gitignore`. **Content verified benign:** the committed logs are fresh-instance bootstrap
logs (component setup timings, one template config error) from a throwaway dev container —
no tokens, usernames, IPs, coordinates, or entity data; a full-history grep for
latitude/longitude/token/password/secret/`192.168.x`/`100.x` found nothing. No history
rewrite is strictly required for content. However, finding #54 recommends publishing
`home-assistant` from a **squashed/clean initial commit** so those runtime logs and
lockfiles never enter public history at all. Also add `config/.cache/` to `.gitignore`.

**Stale URL references (finding #54).** Workspace `CLAUDE.md` and memory cite the
`anirudhlath/alfred-signal-bridge` and `alfred-home-assistant` URLs as if live, but the
repos do not exist yet; `alfred/README.md` line 207 is honest (*"not yet public"*).

Locations flagged (LOC): `signal-bridge/`, `home-assistant/`,
`signal-bridge/pyproject.toml`, `signal-bridge/app/bridge.py`,
`home-assistant/config/configuration.yaml`, `home-assistant/docker-compose.yml`,
`git-history:27d4e12:config/home-assistant.log` (+ `.log.1`, `.HA_VERSION`, `.ha_run.lock`,
`blueprints/`), `git-history:5da91fc^:config/home-assistant.log`.

## Acceptance Criteria
- [ ] Make an explicit license decision for each repo, then add a `LICENSE` file to both `signal-bridge/` and `home-assistant/` before creating their public GitHub repos — MIT to match `alfred-ios`/`home-service`, or AGPL-3.0 to match the `alfred-sdk` dependency.
- [ ] If `signal-bridge` is licensed MIT, add a `license` field to its `pyproject.toml` and document (in its README/LICENSE notes) that deployments combining it with `alfred-sdk` are governed by the SDK's AGPL-3.0 terms.
- [ ] Add a short README to both repos; `signal-bridge`'s README states its license and that it is a scaffold with unwired TODOs.
- [ ] Add a `.gitignore` to `signal-bridge` covering `.env`, `.venv/`, `__pycache__/`, `*.egg-info/` (copy `home-service`'s) so the documented `.env` workflow (a real `SIGNAL_PHONE_NUMBER`) cannot be swept in by `git add .`.
- [ ] Add `config/.cache/` to `home-assistant`'s `.gitignore`.
- [ ] Publish `home-assistant` from a squashed/clean initial commit so the previously-committed HA runtime files (`home-assistant.log`, `.HA_VERSION`, `.ha_run.lock`, `blueprints/` from `27d4e12`) never enter public history. (Content is verified benign, so this is history hygiene, not secret remediation — and no remote rewrite is needed since the repo has no remote yet.)
- [ ] Update the workspace `CLAUDE.md` / memory references to `alfred-signal-bridge` and `alfred-home-assistant` so they match reality (the cited GitHub URLs currently point to repos that do not exist).
