# CI: Add Frontend Gates (npm lint/test/build) + Concurrency Cancellation

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Source:** GitHub agent-enablement round-up 2026-07-18 (verified against `.github/workflows/ci.yml`)

## Summary
`ci.yml` runs only the Python gates (ruff check/format, mypy, pytest). The documented
frontend gates — `npm run lint`, `npm run test`, `npm run build` in `web/` — are **not run
in CI at all**, so a PR that breaks the SPA gets a green checkmark. Agent-driven development
makes CI the sole arbiter of "done"; every documented gate must be enforced or agents will
happily auto-merge broken frontend changes. Also missing: a `concurrency` group, so
superseded runs from rapid agent pushes waste runner time and delay feedback.

## Context / Motivation
- CLAUDE.md's own gotcha list admits the gap: the `mount_spa` route-shadowing bug class
  "Tests don't catch this because `web/dist/` doesn't exist in CI." Building the SPA in CI
  and making it available to the backend test job (same job, or artifact hand-off) closes
  that blind spot.
- Frontend job shape: `actions/setup-node` (Node 22, npm cache keyed on
  `web/package-lock.json`), `npm ci`, then lint / test / build with
  `working-directory: web`.
- Concurrency:

  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: ${{ github.event_name == 'pull_request' }}
  ```

  (Cancel superseded PR runs; never cancel master pushes.)
- Keep total PR feedback time low — agents iterate on CI results, so a slow pipeline
  directly slows development. Python and frontend jobs should run in parallel.
- SHA-pinning and `permissions` for any new actions per
  [harden-github-actions-and-ci](../medium/harden-github-actions-and-ci.md).

## Acceptance Criteria
- [ ] CI runs `npm run lint`, `npm run test`, and `npm run build` in `web/` on every PR and
  master push, in a job parallel to the Python job.
- [ ] The backend pytest job exercises the SPA-mounted path: `web/dist/` exists when pytest
  runs (build in-job or via artifact), so `mount_spa` registration is no longer a CI blind
  spot — with a test asserting the SPA routes register after `/api/auth/*`.
- [ ] `concurrency` group added: pushing a new commit to a PR cancels the in-flight run for
  that PR; master runs are never cancelled.
- [ ] npm dependencies cached; total PR wall-clock stays under ~10 minutes.
- [ ] Both jobs are named stably (e.g. `python`, `web`) and feed the single `ci-ok`
  aggregate gate job from
  [branch-protection-rulesets-and-merge-gating](../medium/branch-protection-rulesets-and-merge-gating.md)
  — only `ci-ok` becomes a required status check.
