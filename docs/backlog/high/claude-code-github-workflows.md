# Claude Code GitHub App + @claude / Auto-Review Workflows

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Source:** GitHub agent-enablement round-up 2026-07-18

## Summary
There is currently no way to dispatch a Claude Code agent from GitHub itself — all agent
work happens in local sessions. Installing the Claude Code GitHub App and adding two
workflows closes the loop: comment `@claude <task>` on any issue or PR and an agent
implements it and opens a PR; every PR additionally gets an automatic Claude review before
the owner looks at it. This is the centerpiece of agent-driven development on these repos —
combined with auto-merge and required checks, the full issue → implementation → review →
merge cycle can run on GitHub.

## Context / Motivation
- Setup path: run `/install-github-app` from the Claude Code CLI, which installs the GitHub
  App and scaffolds the workflow. Auth via `CLAUDE_CODE_OAUTH_TOKEN` (Pro/Max subscription
  — matches the owner's cost preference) or `ANTHROPIC_API_KEY`, stored as an Actions
  secret.
- Two workflows on the `anthropics/claude-code-action` (v1 line):
  - `claude.yml` — tag mode: responds to `@claude` mentions in issue/PR comments and issue
    bodies. Needs `contents: write`, `pull-requests: write`, `issues: write`, `actions: read`
    (to read CI results), `id-token: write`.
  - `claude-review.yml` — triggers on `pull_request` opened/synchronize and posts a code
    review. Read-only permissions plus `pull-requests: write` for the review comment.
- **Public-repo security:** keep the action's default trigger gating (only users with write
  access can invoke `@claude`). Issue/comment bodies from strangers are untrusted prompt
  input — never widen the author-association gate, and don't add `pull_request_target`
  variants. SHA-pin the action per the standards in
  [harden-github-actions-and-ci](../medium/harden-github-actions-and-ci.md).
- The `CLAUDE.md` files are the agent's onboarding — alfred's is strong; the review workflow
  benefits from the sibling-repo CLAUDE.md ticket landing too.
- Verify current action inputs/behavior against the official docs at implementation time
  (context7 / docs.claude.com) — action inputs have evolved across versions.

## Acceptance Criteria
- [ ] Claude Code GitHub App installed on `anirudhlath/alfred` (and alfred-ios +
  home-service if desired) via `/install-github-app`.
- [ ] Auth secret (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`) configured as an
  Actions secret — never committed.
- [ ] `claude.yml` added: `@claude` mention on an issue produces a working branch + PR;
  verified end-to-end with a real trivial task.
- [ ] `claude-review.yml` added: opening a PR produces an automatic Claude review comment;
  verified on a real PR.
- [ ] Trigger gating confirmed: a comment from a non-collaborator account does NOT invoke
  the agent.
- [ ] Both workflows SHA-pinned with least-privilege `permissions` blocks.
- [ ] CLAUDE.md note added documenting the `@claude` workflow so future sessions know
  GitHub-dispatched agents exist.
