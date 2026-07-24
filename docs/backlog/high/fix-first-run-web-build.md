# Fix Broken First-Run: Document Node.js + Web Build Step

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** high
**Severity (audit):** high
**Source:** Public-release readiness audit 2026-07-18 (findings #6)

## Summary
The native (non-container) README quickstart produces a UI-less server: a stranger following the native Setup steps (`uv sync` + start Redis/Mosquitto + `python -m runner`) lands on a channels process where `/` returns 404, because the web SPA is served from the gitignored `web/dist` and `mount_spa()` is a no-op when that directory is missing. The README never mentions Node.js, npm, or the `cd web && npm run build` step needed to produce it. The containerized quickstart (`uv run alfredctl up`, added in Part 2 of containerization) sidesteps this — the fat image's `webbuild` stage bakes `web/dist` in — but the native path is still broken, and the alfred repo is already public, so this is a live first-run defect for anyone choosing native dev, not a pre-release gap; the only recovery hint they get is a runtime log warning.

## Context / Motivation
- The web SPA is served from `web/dist`, which is gitignored (`alfred/.gitignore` line 45: `web/dist/`), so a fresh clone has no built frontend.
- `mount_spa()` in `alfred/core/channels/spa.py` is a no-op when `web/dist` is missing, so `/` 404s on the channels process.
- Grepping the README for `npm`/`node`/`vite` returns zero hits — the Node.js/npm prerequisite and the `cd web && npm run build` step are entirely undocumented.
- `alfred/docs/web-frontend.md` documents the build, but the README does not link to it, so there is no discoverable path to the fix. The only in-band signal is a runtime log warning.
- Rated high severity because it breaks the documented first-run for anyone cloning the (already-public) repo, yielding a server with no usable UI.

## Acceptance Criteria
- [ ] Add Node.js (with npm) to the README Prerequisites section.
- [ ] Add a `cd web && npm install && npm run build` step to the README Run section so `web/dist` is produced before starting the runner.
- [ ] Link `docs/web-frontend.md` from the README so the fuller frontend build docs are discoverable.
- [ ] After following the README from a clean clone, `/` serves the SPA (no 404) with no reliance on the runtime log warning.
