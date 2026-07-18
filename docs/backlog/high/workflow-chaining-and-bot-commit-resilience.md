# Workflow Chaining & Bot-Commit Resilience (GITHUB_TOKEN, Formatting Fixes, skip-ci)

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Source:** GitHub agent-enablement round-up 2026-07-18 (follow-up pass)

## Summary
GitHub's recursion guard — **events created with the workflow `GITHUB_TOKEN` do not
trigger other workflows** — silently breaks three links in the automation chain this epic
builds: (1) a workflow that pushes a formatting/fix-up commit to a PR branch produces a
commit that CI never runs on, so the required check never reports and auto-merge never
fires; (2) auto-merge enabled with `GITHUB_TOKEN` produces a master merge whose `push`
workflows don't run; (3) a tag created inside a workflow with `GITHUB_TOKEN` never fires
the tag-triggered release workflow. Each failure is invisible — nothing errors, things
just don't happen. This ticket sets the token strategy and the formatting policy so every
chain link actually fires.

## Context / Motivation
- **Token strategy.** Where a workflow's output must trigger further workflows, don't use
  `GITHUB_TOKEN` — mint a short-lived **GitHub App installation token** (small dedicated
  bot App via `actions/create-github-app-token`, App ID + private key as secrets) or a
  fine-grained PAT. Escape hatches where a token swap isn't wanted: `workflow_run`
  triggers, or explicit `workflow_dispatch` calls (both work from within workflows).
  Verify current chaining rules against GitHub docs at implementation.
- **Formatting policy: check, don't fix, in CI.** Auto-fix-push workflows are the worst
  fit for agent repos: they need the token workaround AND they race the agent's own pushes
  (non-fast-forward rejections mid-session). Instead: CI *fails* on `ruff format --check` /
  lint, and formatting happens **before commit, locally** — wire a Claude Code hook
  (`alfred/.claude/hooks` already exists) or pre-commit config running
  `ruff check --fix && ruff format` (and `npm run lint` for `web/`) so agent commits
  arrive formatted. The `@claude` Actions agent runs the same repo hooks. Auto-fix-push
  stays banned unless a concrete need appears — and then only with an App token +
  `concurrency` guard.
- **Auto-merge actor.** [dependabot-automerge-workflow](../medium/dependabot-automerge-workflow.md)
  calls `gh pr merge --auto` — if authorized with `GITHUB_TOKEN`, the eventual merge is
  attributed to `github-actions[bot]` and master `push` workflows (master CI, future
  deploy/release chains) don't run. Use the bot App token for enabling auto-merge, or
  consciously accept no post-merge workflows on those merges and document it.
- **Release chain.** [release-please-setup](../medium/release-please-setup.md) creates
  tags/Releases from its workflow: with `GITHUB_TOKEN` those events can never trigger
  downstream workflows (future deploy hooks) — release-please must run with the bot App
  token.
- **`[skip ci]` policy.** Commit messages containing `[skip ci]`/`[no ci]` suppress
  workflow runs — on a PR branch with required checks, that means the check never reports
  and the PR blocks (or worse, an earlier green stands while the tip is untested).
  Document in CONTRIBUTING/CLAUDE.md: never use skip-ci markers on PR branches; agents
  must not emit them.
- **Reusable workflows for the sister repos.** When
  [harden-github-actions-and-ci](../medium/harden-github-actions-and-ci.md) adds CI to
  home-service (same uv/ruff/mypy/pytest shape as alfred), extract a reusable
  `workflow_call` Python-CI workflow (public repos can call across repos) so the gates
  stay in lockstep instead of drifting per-repo.

## Acceptance Criteria
- [ ] Dedicated bot GitHub App created (or fine-grained PAT provisioned), credentials
  stored as Actions secrets; used wherever a workflow's output must trigger workflows.
- [ ] Auto-merge enablement (Dependabot workflow, any agent flows) uses the App token;
  verified a bot-initiated merge to master triggers master `push` workflows.
- [ ] Local formatting enforced pre-commit via Claude Code hook or pre-commit config
  (ruff check+format; web lint); CI remains check-only; no auto-fix-push workflows exist.
- [ ] Verified end-to-end: an agent commit made through the hook path arrives formatted
  and CI passes without any fix-up bot commit.
- [ ] skip-ci policy documented in CONTRIBUTING + CLAUDE.md (agents and humans: never on
  PR branches).
- [ ] Shared Python CI extracted as a reusable workflow when home-service CI lands, called
  by both repos.
