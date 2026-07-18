# Epic: GitHub Chores — Workflows, Settings & Protection

**Status:** Open
**Created:** 2026-07-18
**Owner:** @anirudhlath
**Source:** Round-up of all GitHub-platform work across the backlog (tickets originate from the
2026-07-18 public-release readiness audit) + live repo-state check on 2026-07-18 post-merge-train.

## Goal
One place for every chore that happens **on GitHub itself** — repo settings, Actions
workflows, branch protection, community-health files, branch/PR hygiene, and new-repo
creation — across the five workspace repos. Most items are free to enable and take minutes;
together they are the guardrails that would have prevented (or would now catch) the `.p8`
key leak.

Beyond hardening, this epic also carries the **agent-enablement track (§6)**: the settings,
workflows, and repo content that let Claude Code agents drive development end-to-end on
GitHub — issue → `@claude` implementation → auto-review → auto-merge — with CI as the
trusted arbiter.

## Relationship to the Alpha Release epic
Tickets in §1–§5 are also linked from [Alpha Release Readiness](alpha-release.md) — the
GitHub-platform *slice* of that work; closing one checks it off in both epics, do not
duplicate. §6 is new scope (2026-07-18 agent-enablement round-up) owned solely by this
epic.

## ⚠️ Handling note
Inherited from the alpha epic: tickets marked 🔒 describe live exposures on already-public
repos. Do not file them as public GitHub issues until remediated. This epic file and the
audit tickets are intentionally **untracked** (local-only) — do not bulk-commit them.

---

## 1 · Repo settings (pure GitHub toggles — do first, ~15 min total)
- [ ] [Enable Secret Scanning, Push Protection & Dependabot on Public Repos](../high/enable-github-security-features.md) **(high)** — secret scanning, push protection, validity checks, non-provider patterns, private vulnerability reporting, Dependabot alerts + security updates on `alfred`, `alfred-ios`, `alfred-home-service`. All currently OFF; each is one `gh api` call (commands are in the ticket).
  - Sequencing: consider landing [dependency-cve-relock](../medium/dependency-cve-relock.md) first (or immediately after) so enabling Dependabot doesn't open ~16 pre-known alerts.

## 2 · Workflows, CI & branch protection
- [ ] [Harden GitHub Actions & Add Branch Protection / CI Enforcement](../medium/harden-github-actions-and-ci.md) **(medium)** — the big one:
  - Branch protection + required status checks on all three default branches (alfred `master`, alfred-ios `master`, home-service `main`).
  - `cla.yml`: drop `actions:write`, SHA-pin `contributor-assistant` (runs on `pull_request_target` with write perms today).
  - `ci.yml`: add top-level `permissions: contents: read`, SHA-pin all actions.
  - Minimal CI workflows for alfred-ios (`swift test`) and home-service (`pytest` + `ruff` + `mypy`) — both currently have **zero** CI.
  - `dependabot.yml` for the github-actions ecosystem.
  - Fork-PR approval → `all_external_contributors`; resolve the DCO promise-vs-practice gap in CONTRIBUTING.
  - Prerequisite: [Restore Green Quality Gates on master](../high/restore-green-master-gates.md) — CI must be reliably green **before** it becomes a required check, or branch protection blocks all merges.
  - Implementation mechanics (rulesets, `ci-ok` aggregate gate, up-to-date strictness, tag/push rules) are specified in [branch-protection-rulesets-and-merge-gating](../medium/branch-protection-rulesets-and-merge-gating.md) — land the two together.

## 3 · Community-health & security-metadata files
- [ ] [Add SECURITY.md, Issue/PR Templates & Code of Conduct](../medium/add-security-md-and-repo-health-files.md) **(medium)** — SECURITY.md pointing at private vulnerability reporting (pairs with §1's PVR toggle), issue/PR templates, CODE_OF_CONDUCT.md, dependabot config. `alfred/.github/` currently holds only the two workflow files; sister repos have nothing.

## 4 · Branch & PR hygiene
- [ ] [Delete Stale Merged Remote Branches (incl. the `>` PCM blob branch)](../medium/delete-stale-remote-branches.md) **(medium, NEW)** — post-merge-train state verified 2026-07-18: the stray `>` audio blob is **NOT** in master (tree + `--find-object` history both clean), but `origin/feature/voice-satellite-bridge` still publishes it, and three other merged branches linger. Deleting them completes the remaining GitHub-side work of [clean-public-pr-branch-artifacts](../medium/clean-public-pr-branch-artifacts.md). Keep `cla-signatures` (CLA bot data branch).
- [ ] 🔒 [Scrub Private Session URLs from PR Bodies/Comments](../low/scrub-local-paths-and-session-links.md) **(low, GitHub slice)** — `gh pr edit` on alfred #27/#28/#29 + alfred-ios #1, and `gh api -X PATCH` on two alfred #28 issue comments to strip claude.ai session deep links. (The docs-path scrub half of that ticket is repo content, not GitHub.)

## 5 · GitHub slices of tickets owned elsewhere
These tickets are driven from the alpha epic; only their GitHub-side actions are listed here
so a GitHub work session can knock them out together.
- [ ] 🔒 [Revoke Leaked APNs Key](../highest/revoke-leaked-apns-key.md) **(highest)** — GitHub side: file the GitHub Support request ("Remove cached commits / sensitive data") to GC the unreachable pre-rewrite objects, then verify `gh api repos/anirudhlath/alfred/commits/2cc1132e` no longer serves the `.p8` blob. (Apple-portal revocation is the blocker half and comes first.)
- [ ] 🔒 [Prep signal-bridge & home-assistant Repos](../medium/prep-sibling-repos-license-readme.md) **(medium)** — GitHub side: create `anirudhlath/alfred-signal-bridge` + `anirudhlath/alfred-home-assistant` (currently the URLs in CLAUDE.md point at repos that don't exist), publish home-assistant from a squashed clean initial commit, then apply §1 settings + §3 files to both new repos.
- [ ] 🔒 [Redact Apartment HA IP](../medium/redact-apartment-ip.md) **(medium)** — GitHub side: the history rewrite lands via force-push to the public repo; coordinate with the APNs GC request (one GitHub Support interaction can cover both purges).

## 6 · Agent enablement (new scope — beyond the audit)
The point of the hardening above isn't just safety — it's making the green checkmark
trustworthy enough that agents can merge on it. These tickets build the automation on top.
The branching/release model they implement is specified in
`docs/superpowers/specs/2026-07-18-branching-strategy-design.md` (trunk + tags, PR-only,
squash-only conventional PR titles, milestones = releases, release-please, QA-gated
release PRs).

**Foundation (high):**
- [ ] [Claude Code GitHub App + @claude / Auto-Review Workflows](../high/claude-code-github-workflows.md) **(high)** — the centerpiece: `@claude` on issues/PRs → implementation PR; automatic Claude review on every PR. `/install-github-app`, subscription OAuth token, write-access-only triggers.
- [ ] [Enable Auto-Merge & Delete-Branch-on-Merge on All Repos](../high/enable-auto-merge-branch-cleanup.md) **(high)** — both verified OFF on alfred; agents can't land their own green PRs and every merge strands a branch. Squash-only recommended. Auto-merge gates on branch protection (§2).
- [ ] [CI: Add Frontend Gates + Concurrency Cancellation](../high/ci-frontend-gates-and-concurrency.md) **(high)** — `npm lint/test/build` don't run in CI at all today, and `web/dist` never exists there (the `mount_spa` blind spot). Must land before CI becomes a required check agents auto-merge on.
- [ ] [Add CLAUDE.md to home-service, signal-bridge & home-assistant](../high/claude-md-for-sibling-repos.md) **(high)** — three repos have none; GitHub-dispatched agents check out a single repo and fly blind. Cheapest item here.
- [ ] [Workflow Chaining & Bot-Commit Resilience](../high/workflow-chaining-and-bot-commit-resilience.md) **(high)** — `GITHUB_TOKEN` events don't trigger workflows: bot-pushed commits get no CI, bot-enabled auto-merges skip master workflows, bot-created tags skip release. Bot App token strategy, check-don't-fix formatting via local Claude Code hooks, skip-ci ban, reusable CI workflows.
- [ ] [Branch Protection Mechanics: Rulesets, Aggregate CI Gate & Merge-Blocking](../medium/branch-protection-rulesets-and-merge-gating.md) **(medium — lands WITH §2's protection work)** — rulesets over classic protection, single `ci-ok` required check (`needs` + `if: always()`) so job renames/path filters never strand PRs on "Expected", up-to-date strictness decision (merge queue unavailable on user-owned repos), `v*` tag ruleset, push rules / CI artifact-guard for the `>`-blob class.
- [ ] [Branching Conventions: Naming, Conventional PR Titles & Worktree Policy](../high/branching-conventions-and-pr-title-check.md) **(high)** — `<type>/<slug>` branches, conventional PR titles enforced in `ci-ok`, PR-only + worktree policy into CONTRIBUTING/CLAUDE.md; commits the branching spec. Foundation for release-please.

**Workflow upgrades (medium, after the foundation):**
- [ ] [Mirror Non-Sensitive Backlog to GitHub Issues as the Agent Work Queue](../medium/github-issues-agent-work-queue.md) **(medium)** — label taxonomy + `agent-ready` tickets `@claude` can be pointed at; 🔒 tickets stay local.
- [ ] [Dependabot Auto-Merge Workflow](../medium/dependabot-automerge-workflow.md) **(medium)** — patch/dev-dep updates land themselves once §1 Dependabot + §2 protection + auto-merge exist; majors and auth/crypto-path deps still wait.
- [ ] [Devcontainer / Environment Bootstrap for Cloud Agents](../medium/devcontainer-cloud-agent-bootstrap.md) **(medium)** — redis-stack + mosquitto + py3.13 + node in a reproducible Linux container so cloud sessions can run the live system, not just the mocked suite.
- [ ] [release-please: Versioning, Changelog & QA-Gated Release PRs](../medium/release-please-setup.md) **(medium)** — rolling release PR computes semver from conventional squash titles; merging it (owner-only, after milestone-empty + QA-backlog-cleared gate) tags and publishes the Release via the bot App token. Requires the conventions ticket first. *(Supersedes the removed tag-push release ticket.)*

**Polish (low):**
- [ ] [CODEOWNERS for Auto Review-Requests](../low/codeowners-auto-review-requests.md) **(low)** — a ping on every agent PR, deliberately NOT a required-review gate (would break auto-merge).

**Standing policy — no self-hosted runners on public repos.** The 4090 box will tempt as a
runner for evals/wake-word training; on a public repo, fork PRs mean arbitrary code
execution on the machine that runs the actual home. If GPU CI ever matters: private mirror
or `workflow_dispatch`-only with environment approval — preferably keep GPU work off
Actions entirely.

## Suggested order
1. **§1 settings toggles** — push protection + secret scanning immediately (no dependencies); hold Dependabot *alerts* until the CVE relock lands if alert noise matters. Flip `delete_branch_on_merge` (§6) in the same pass — it has no prerequisites.
2. **§4 branch deletion** — one `git push origin --delete` sweep; removes the published blob.
3. **Cheap agent groundwork (§6)** — sibling-repo CLAUDE.md files + CI frontend gates + concurrency; these are normal PRs with no dependencies and everything later leans on them.
4. **§3 health files + §2 workflow hardening** (SHA-pinning, permissions, sister-repo CI) — normal PRs.
5. **§2 branch protection**, once master's gates are green (incl. the new frontend job) and CI is a trustworthy required check — then flip `allow_auto_merge` (§6) behind it.
6. **§6 automation on top** — Claude Code App + @claude/review workflows, then Dependabot auto-merge, then Issues-as-queue; devcontainer + polish items anytime.
7. **§5 slices** ride along with their owning tickets (Apple revocation → GitHub Support GC; repo creation when sibling prep is done — apply §1/§3/§6 settings to new repos at creation).

## Autonomous-execution notes (owner plans to run this epic in auto mode)
The epic is designed to be agent-executable end-to-end EXCEPT the steps below. The
executor should complete everything else, queue these up, and hand back one consolidated
owner checklist rather than stalling mid-run:

**Owner-only (cannot be done by an agent):**
- Apple Developer portal: revoke APNs key `2U36353CR2` + issue the replacement (web-only;
  the epic's blocker — everything else can proceed in parallel, but this cannot wait).
- GitHub Support GC request submission (agent drafts the text; owner files it under
  their account).
- `/install-github-app` (interactive App install + OAuth) and creating the bot GitHub
  App; storing `CLAUDE_CODE_OAUTH_TOKEN` / App private key as Actions secrets — owner
  handles all secret material.
- Manual QA passes (`docs/qa-backlog/` — real mic, real devices, real home).
- Merging release-please PRs (the release act is owner-only by design).

**Standing rules for the auto run:**
- 🔒 tickets are coordinated disclosure: never filed as public issues, never committed,
  their specifics never quoted in public PRs/commits.
- Follow the branching spec from the FIRST PR of the run: worktree per branch,
  `<type>/<slug>` naming, conventional PR title, squash, delete branch+worktree on merge.
  The main checkout stays parked on the trunk.
- Sequence hard dependencies: green gates → `ci-ok` → protection → auto-merge →
  conventions check → release-please. Don't enable auto-merge before required checks
  exist.
- Verify each settings change by reading it back (`gh api`), not by assuming success.

## Definition of Done
**Hardening:**
- Secret scanning, push protection, private vulnerability reporting, and Dependabot (alerts + security updates) enabled on **every** repo with a GitHub remote.
- Branch protection with required CI on all default branches; CLA required on alfred.
- All Actions workflows SHA-pinned, least-privilege `permissions`, fork-PR approval tightened; alfred-ios and home-service run CI on PRs.
- SECURITY.md + templates + CoC + dependabot.yml present in each public repo.
- No merged/stale feature branches on origin; the `>` blob is no longer published anywhere.
- No claude.ai session links in public PR bodies/comments.
- GitHub Support GC request filed and verified for the pre-rewrite `.p8` objects.

**Agent enablement:**
- CI enforces **every** documented gate (Python + frontend) and is fast enough to iterate on (<~10 min PR feedback).
- An issue labeled `agent-ready` can go issue → `@claude` → PR → auto Claude review → auto-merge → auto branch-delete with zero manual clicks — demonstrated at least once end-to-end.
- Dependabot patch updates land themselves; majors and auth/crypto-path deps wait for review.
- Every repo an agent can be dispatched to has a standalone CLAUDE.md.
- A cloud agent session can bootstrap the full dev environment from the repo alone (devcontainer).
- No self-hosted runners attached to any public repo (standing policy).
- The merge gate is drift-proof and the chain has no dead links: a single `ci-ok` required check (job renames/path filters can't strand PRs on "Expected"), agent commits arrive pre-formatted (no fix-up bot commits), and every bot-produced event — commit, auto-merge, tag — demonstrably triggers its downstream workflows.
- The branching spec is live end-to-end: every PR follows `<type>/<slug>` + conventional title + squash, and one release has been cut through the full cycle — milestone emptied → QA backlog cleared → release-please PR merged → tagged Release published → prod deploys the tag.
