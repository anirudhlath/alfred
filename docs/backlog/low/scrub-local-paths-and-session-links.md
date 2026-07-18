# Scrub Local Absolute Paths & Private Session URLs from Published Docs/PRs

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** low
**Severity (audit):** low
**Source:** Public-release readiness audit 2026-07-18 (findings #42, #51, #66)

## Summary

Tracked design/plan docs embed the owner's local absolute paths
(`/Users/anirudhlath/code/private/alfred/...`), and several `docs/superpowers/plans/`
files are internal execution logs (git-worktree/PR workflow, a personal `~/.claude`
config plan, a plan for a repo that is not published yet) rather than user-facing docs.
Separately, six already-public PR bodies and comments carry private Claude Code
cloud-session deep links. Because the three repos are already public, this is
post-exposure cleanup, not pre-release prep. None of it is a live secret: the username
matches the public GitHub handle (identity leakage is nil), and the session links are
auth-gated (no content leaks to third parties) — so this is privacy-hygiene and polish,
not a security incident. No history rewrite is warranted for these findings alone.

## Context / Motivation

**#42 — local absolute paths in docs (`local-abs-paths-docs`):** 307 occurrences of
`/Users/anirudhlath/code/private/...` across 18 tracked docs (mostly implementation
plans), e.g. `Run: cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest ...`.
This reveals the local macOS home directory and private workspace layout and reads as
unpolished in a public repo; it is purely cosmetic/privacy-hygiene. Locations include
`docs/superpowers/plans/2026-03-10-basefeature-dynamic-tools.md`,
`docs/superpowers/plans/2026-03-10-phase1-foundation.md`,
`docs/superpowers/plans/2026-03-19-phase3-step2-conscious-engine.md`,
`docs/superpowers/plans/2026-07-15-ha-plan2-home-service-rewrite.md`, and
`docs/superpowers/specs/2026-03-10-project-alfred-design.md`.

**#51 — internal plans published (`internal-plans-published`):** 18 tracked plan files
are internal execution logs, not user docs — dozens of literal
`cd /Users/anirudhlath/code/private/alfred/...` commands (the basefeature plan alone has
~20), git-worktree instructions, and PR-number history.
`docs/superpowers/plans/2026-04-09-claude-config-consolidation.md` is entirely about the
owner's personal `~/.claude` configuration and unrelated to the product;
`docs/superpowers/plans/2026-07-16-alfred-satellite-repo-plan.md` plans a repo
(`alfred-satellite`) that does not exist publicly. None of this is secret, but it
confuses a stranger browsing the repo. Locations also cited:
`docs/superpowers/plans/2026-03-10-context-provider.md` and
`docs/backlog/medium/mypy-strict-redis8-stub-drift.md`.

**#66 — Claude session links in PR metadata (`claude-session-links-in-pr-metadata`):**
Six claude.ai Claude Code cloud-session deep links in already-public PR metadata:
`session_016huNVK6cXmBUMa635GFZyb` (alfred #29), `session_014hYXHkZCPVjtsHXufhmpRZ`
(alfred #28), and `session_01HEc6iM6W9L8cqbeMrgV3JF` (alfred #27, both alfred #28 issue
comments, and alfred-ios #1 — the same session ID cross-posted to two repos). The links
are auth-gated today and leak no content to third parties, but they permanently publish
the owner's private session identifiers. Locations: alfred PR #29 body, #28 body, #27
body; alfred #28 comments `issuecomment-4995176005` and `issuecomment-4997925093`;
alfred-ios PR #1 body.

## Acceptance Criteria

- [ ] Replace `/Users/anirudhlath/code/private/alfred` with a repo-relative path or a
      `$REPO` placeholder across `docs/superpowers/`; no local absolute home-directory
      paths remain in any tracked doc.
- [ ] Decide the `docs/superpowers/plans/` disposition: either prune the execution-plan
      files (keeping the specs) or add a `docs/superpowers/README.md` disclaiming them as
      historical internal working documents; whatever is kept has its absolute paths
      replaced with repo-relative ones.
- [ ] Remove or relocate `docs/superpowers/plans/2026-04-09-claude-config-consolidation.md`
      (owner's personal `~/.claude` config, unrelated to the product), and remove or
      annotate `docs/superpowers/plans/2026-07-16-alfred-satellite-repo-plan.md` (plans
      the not-yet-public `alfred-satellite` repo).
- [ ] Strip the Claude Code session URL lines from the three PR bodies
      (`gh pr edit 27 28 29 -R anirudhlath/alfred` filtering out `claude.ai/code` lines,
      and `gh pr edit 1 -R anirudhlath/alfred-ios`) and edit the two alfred #28 issue
      comments (`issuecomment-4995176005`, `issuecomment-4997925093`) via
      `gh api -X PATCH`.
- [ ] No standalone git-history rewrite is performed for these findings (no live secret);
      if a `git filter-repo` pass runs for the separate IP/secret findings, fold the
      absolute-path scrub into that pass.
