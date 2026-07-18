# Branching Strategy & Release SDLC — Design

**Date:** 2026-07-18
**Status:** Approved
**Scope:** All workspace repos (alfred, alfred-ios, home-service, and the future
signal-bridge / home-assistant publics)
**Driver:** Solo owner + heavy agent automation (auto-merge, @claude workflows, worktree
sessions). Guiding constraint from the owner: no ceremony that slows the pipeline.

## Decisions (settled during brainstorming)
1. **PR-only, no exceptions** — every change (code, docs, specs) reaches the trunk via a
   PR with required checks; ruleset owner-bypass is for emergencies only.
2. **Hybrid PR granularity** — plan-scoped PRs for designed features; plans over ~a day
   split into sequentially landable PRs; fixes/chores/agent tasks ship as small PRs.
3. **Trunk + release tags** (no develop branch) — releases are QA-gated, not continuous.
4. **Milestones = releases, labels = epics** on GitHub.
5. **release-please** computes versions and cuts releases.

## 1 · Branch model
One long-lived branch per repo: the existing default (`master` on alfred/alfred-ios,
`main` on home-service). No renames. All other branches are short-lived topic branches
deleted at merge. Ruleset per repo: PRs required, single `ci-ok` required check,
force-push/deletion blocked, owner-only bypass (logged, emergencies only).

## 2 · Branch naming
`<type>/<ticket-slug>` with type ∈ `feat | fix | chore | docs | refactor | test | ci |
perf` (conventional-commit types), slug matching the backlog ticket / GitHub issue.
Examples: `feat/voice-satellite-bridge`, `ci/frontend-gates`, `docs/model-licenses`.
Reserved prefix: `claude/` — GitHub-App-created branches keep the action's native
`claude/issue-N-…` naming. Replaces the historical `feature/`-vs-`feat/`-vs-bare drift.

## 3 · Commits & merging
- **Squash-only.** Merge commits and rebase-merge disabled on all repos.
- **PR title = conventional commit line** (`feat(voice): add satellite barge-in`,
  breaking = `!` after type/scope). It becomes the squash commit on the trunk and feeds
  release-please + categorized release notes.
- **No commitlint on intermediate commits** — they are squashed away; enforcement is one
  CI step validating the PR title, part of `ci-ok`.
- Branches auto-delete on merge and are never reused (squash breaks lineage).

## 4 · Worktree discipline
- The main checkout stays parked on the trunk, pull-only; it never commits.
- One worktree per topic branch, created inside the owning repo (never the workspace
  root); tool chooses the directory (`.worktrees/` or `.claude/worktrees/`).
- The worktree is deleted as soon as its PR merges.

## 5 · Work decomposition
Spec → epics → tickets → PRs. On GitHub: **milestone per release** collects the issues
gating it (empty milestone = scope complete); **`epic:<name>` labels** group tickets
across releases; one ticket ↔ one PR as the norm. Epic markdown files
(`docs/backlog/epics/`) remain the narrative + sensitive layer; milestones/labels are the
operational layer agents see. Large specs must decompose before implementation.

## 6 · Release SDLC (trunk + tags, QA-gated, release-please)
- Development is continuous: PRs auto-merge into the trunk when `ci-ok` passes. The
  trunk is always CI-green but not automatically release-ready.
- **release-please** (per repo) maintains a rolling release PR from the squash-commit
  history: computed semver bump, CHANGELOG, version-file updates (`pyproject.toml` for
  alfred/home-service; a version manifest for alfred-ios).
- The release PR is **never auto-merged**. Release gate, in order:
  1. Release milestone has zero open issues.
  2. Manual QA pass: every `docs/qa-backlog/` ticket for the milestone's features is
     verified and deleted (existing convention, now the formal gate).
  3. Owner merges the release PR → release-please tags `vX.Y.Z` and publishes the
     GitHub Release with categorized notes.
- **Prod (CachyOS) deploys pinned tags only — never the trunk.**
- The release-please workflow uses the bot App token (per the workflow-chaining ticket)
  so its tags/releases can trigger downstream workflows if any are added later.

### Semantic versioning rules
- Pre-1.0 (`0.x`): `feat` → minor; `fix`/`chore`/etc → patch; **breaking → minor**
  (release-please `bump-minor-pre-major: true`), flagged with `!` in the PR title.
- **Breaking** means: a coordinated change is required in a sibling repo (iOS client,
  SDK consumers) or a manual migration on the prod box (Redis schema, config formats).
- Each repo versions independently; cross-repo compatibility is declared in release
  notes ("requires alfred ≥ 0.2"), not lockstep versions.
- 1.0 is deferred until the SDK/API is stable for external users; hotfixes are normal
  fix PRs + a patch release from the trunk (branch-from-tag only if the trunk ever
  carries unshippable work — no standing process for it).

## 7 · Non-goals (rejected as ceremony)
No develop branch, no release branches, no GitFlow, no commitlint hooks, no merge queue
(unavailable on user-owned repos anyway), no stacked-PR tooling, no default-branch
renames, no cross-repo version lockstep.

## Ticket mapping (github-chores epic)
- `high/branching-conventions-and-pr-title-check` — §2/§3/§4 conventions into
  CONTRIBUTING + CLAUDE.md, PR-title validation in `ci-ok` (new).
- `medium/release-please-setup` — §6 (new; supersedes the tag-push release ticket).
- `high/enable-auto-merge-branch-cleanup` — squash-only now definitive (§3).
- `medium/branch-protection-rulesets-and-merge-gating` — §1 mechanics (existing).
- `medium/github-issues-agent-work-queue` — extended with milestone-per-release (§5).
- `medium/delete-stale-remote-branches` — clears pre-strategy branch debt (existing).
