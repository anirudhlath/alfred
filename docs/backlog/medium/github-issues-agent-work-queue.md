# Mirror Non-Sensitive Backlog to GitHub Issues as the Agent Work Queue

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Source:** GitHub agent-enablement round-up 2026-07-18

## Summary
The backlog lives in local markdown files (`docs/backlog/<priority>/`), which no
GitHub-dispatched agent can see or be assigned to. Mirroring the **non-sensitive** tickets
into GitHub Issues with a label taxonomy turns the `@claude` workflow
([claude-code-github-workflows](../high/claude-code-github-workflows.md)) into a real work
queue: open issue → `@claude` mention (or scheduled dispatch) → PR → auto-review →
auto-merge, entirely on GitHub. Sensitive tickets (🔒 coordinated-disclosure items) stay
local by design.

## Context / Motivation
- **Sensitivity split is the hard constraint.** The alpha-release epic's handling note
  keeps live-exposure tickets out of public issues until remediated. Concretely: 🔒 tickets
  and this epic file remain local; everything else is mirrorable. Re-check each ticket
  against the note before mirroring — when in doubt, keep it local.
- **Source-of-truth decision (make explicitly):** recommended — GitHub Issues become
  canonical for mirrored tickets (close = done, delete the local file); local files remain
  canonical only for sensitive tickets. A half-mirrored backlog with two truths is worse
  than either extreme.
- Label taxonomy mapping the existing tiers: `priority: highest|high|medium|low|lowest`,
  plus `epic: alpha-release`, `epic: github-chores`, and type labels (`security`, `ci`,
  `agent-ready`). An `agent-ready` label marks tickets scoped tightly enough to hand to
  `@claude` without discussion.
- **Milestones = releases** (branching strategy spec §5/§6): a milestone per upcoming
  release (e.g. `v0.2.0`) collects the issues gating it; zero open issues = scope
  complete and the QA pass can start. Epics stay as labels because they span releases.
- The issue templates from
  [add-security-md-and-repo-health-files](add-security-md-and-repo-health-files.md) become
  the structured input format — acceptance-criteria checklists translate directly.
- Mirroring is scriptable with `gh issue create` from the ticket files (title from H1,
  body from the file, labels from the directory). Sub-issues / task-list issues can model
  the epics.

## Acceptance Criteria
- [ ] Source-of-truth policy decided and written into `docs/backlog/README.md` (or the
  epic files): what lives in Issues, what stays local, and what "done" means in each.
- [ ] Label taxonomy created on `anirudhlath/alfred` (priority tiers + epics + types +
  `agent-ready`), and a milestone created for the next release with its gating issues
  assigned.
- [ ] All non-sensitive open tickets mirrored as issues with correct labels; 🔒 tickets and
  epic files verified NOT mirrored.
- [ ] Epics represented (labels or task-list parent issues) so per-epic progress is
  visible on GitHub.
- [ ] At least one `agent-ready` issue driven end-to-end via `@claude` to validate the
  queue actually works.
- [ ] Backlog convention docs (workspace CLAUDE.md "Backlog" section) updated to reflect
  the hybrid model.
