# Branching Conventions: Naming, Conventional PR Titles & Worktree Policy

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Source:** Branching strategy spec `docs/superpowers/specs/2026-07-18-branching-strategy-design.md` (§2–§4)

## Summary
Implement the convention layer of the branching strategy: branch naming
(`<type>/<ticket-slug>`, conventional-commit types, `claude/` reserved for App-created
branches), conventional-commit **PR titles** enforced by a CI check inside `ci-ok`, and
the worktree/PR-only policy written into CONTRIBUTING and every CLAUDE.md so humans and
agents inherit identical rules. This is the foundation release-please depends on —
squash commits are only as good as the PR titles that become them.

## Context / Motivation
- Historical drift to kill: `feature/` vs `feat/` vs `fix/` vs bare names
  (`phase1-live-runner`) across 29 merged PRs.
- PR-title check: validate `type(scope)!?: subject` with type ∈
  `feat|fix|chore|docs|refactor|test|ci|perf` — use `amannn/action-semantic-pull-request`
  (SHA-pinned, least privilege) or an equivalent ~10-line script step; it must be a job
  feeding `ci-ok`, not a separate required check. Deliberately NO commitlint on
  intermediate commits (they squash away).
- Docs to update:
  - `alfred/CONTRIBUTING.md` — branch naming, PR-title format, squash-only, PR-only (no
    direct pushes), breaking-change `!` definition (sibling-repo coordination or prod
    migration).
  - CLAUDE.md in each repo + workspace CLAUDE.md — same rules, plus worktree policy:
    main checkout parked on trunk and pull-only, one worktree per topic branch inside
    the owning repo, delete worktree immediately after its PR merges.
- The spec's decision log covers rejected alternatives (no develop branch, no
  commitlint, no renames) — link it rather than restating.

## Acceptance Criteria
- [ ] PR-title validation runs on every alfred PR (opened/edited/synchronize), fails on
  non-conventional titles, and is aggregated into `ci-ok`.
- [ ] The same check is included in the minimal CI of alfred-ios and home-service when
  those workflows land ([harden-github-actions-and-ci](../medium/harden-github-actions-and-ci.md)).
- [ ] CONTRIBUTING.md documents branch naming, PR-title format, squash-only, and the
  PR-only policy; the stale DCO paragraph is resolved in the same edit (per the harden
  ticket's AC).
- [ ] Workspace + per-repo CLAUDE.md files document branch naming and the worktree
  policy (pull-only main checkout, worktree per branch, delete on merge).
- [ ] Spec is committed to the repo as part of this ticket's PR
  (`docs/superpowers/specs/2026-07-18-branching-strategy-design.md`).
- [ ] Verified: a PR titled `update stuff` fails the check; `feat(ci): add title check`
  passes.
