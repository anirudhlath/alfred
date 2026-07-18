# Add CLAUDE.md to home-service, signal-bridge & home-assistant

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Source:** GitHub agent-enablement round-up 2026-07-18 (verified: no CLAUDE.md in any of the three)

## Summary
Three of the five workspace repos — `home-service/`, `signal-bridge/`, `home-assistant/` —
have no `CLAUDE.md`. Any agent landing there (local session, `@claude` GitHub workflow, or
cloud session) flies blind: no run/test commands, no architecture pointers, no gotchas.
This is the cheapest, highest-leverage item in the agent-enablement set — alfred's own
CLAUDE.md is the model for how much it helps.

## Context / Motivation
Content per repo (keep them short — these are small repos):
- **home-service** — FastAPI HA wrapper: how to run (`uv run uvicorn app.server:app --port
  8000`), test/lint/type commands, `.env` requirements (HA URL/token, python-dotenv), the
  alfred-sdk dependency (copied from `alfred/sdk/` in container builds, not PyPI), and its
  role as a sovereign app (SDK-only coupling). Note the Plan 2 rewrite
  (`2026-07-15-ha-plan2-home-service-rewrite.md`) will reshape it — write CLAUDE.md to
  match reality at time of writing.
- **signal-bridge** — scaffold status (unwired TODOs), signal-cli subprocess design,
  `.env` workflow (real phone number — never commit), planned inbound/outbound flow to
  `USER_REQUESTS_STREAM`.
- **home-assistant** — dev/testing HA config: template entities for virtual devices, the
  `level`/`set_level` template-light gotcha, MQTT-via-UI (not YAML), docker-compose usage,
  and that it is NOT the live apartment HA.
- The `/init` skill can bootstrap each file; hand-tune afterward. Workspace-level and
  global conventions (uv, ruff, mypy --strict, loguru) already flow from the parent
  CLAUDE.md files for local sessions — but GitHub-dispatched agents check out a single
  repo, so each repo's CLAUDE.md must stand alone on essentials.

## Acceptance Criteria
- [ ] Each of the three repos has a CLAUDE.md covering: what the repo is, how to run it,
  how to test/lint it, key gotchas, and its relationship to the alfred monorepo/SDK.
- [ ] Each stands alone (no reliance on workspace-parent CLAUDE.md files that
  GitHub-dispatched agents won't have).
- [ ] signal-bridge's CLAUDE.md explicitly warns that `.env` holds a real phone number and
  must never be committed (pairs with its missing-`.gitignore` fix in
  [prep-sibling-repos-license-readme](../medium/prep-sibling-repos-license-readme.md)).
- [ ] home-assistant's CLAUDE.md states it is the dev config, not the live apartment
  instance.
