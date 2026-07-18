# Contributing

Thanks for your interest in Alfred!

## License and CLA

Alfred is licensed under [AGPL-3.0-or-later](LICENSE). By contributing, you agree that your
contributions are licensed under the same terms.

Contributions additionally require a one-time [Contributor License Agreement](CLA.md)
signature. On your first pull request the CLA bot will prompt you; sign by replying with
the comment it shows. The CLA licenses your contribution to the project owner (you keep
ownership of it) and preserves the project's ability to be relicensed or dual-licensed.

## Development

```bash
uv sync --all-extras
uv run ruff check . && uv run ruff format --check .
uv run mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
uv run pytest
cd web && npm run lint && npm run test && npm run build   # if you touched the frontend
```

CI runs exactly these gates plus a PR-title check, aggregated into a single required
`ci-ok` check.

## Branches, PRs, and merging

- **Every change lands via a pull request** — no direct pushes to the default branch.
- **Branch naming:** `<type>/<short-slug>` with type one of
  `feat | fix | chore | docs | refactor | test | ci | perf`
  (e.g. `feat/voice-satellite-bridge`, `ci/frontend-gates`). The `claude/` prefix is
  reserved for GitHub-App-created branches.
- **PR title = conventional commit line:** `type(scope)!?: subject` — it becomes the
  squash commit on the trunk and drives release notes and versioning. `!` marks a
  breaking change (requires coordinated sibling-repo changes or a manual prod
  migration).
- **Squash-only.** Intermediate commit messages don't matter; the PR title does.
- Branches are deleted on merge and never reused.
- **Never use `[skip ci]`/`[no ci]` markers on PR branches** — required checks would
  never report and the PR blocks (or merges with an untested tip).
- Releases: release-please maintains a rolling release PR; merging it (owner-only,
  after the release milestone is empty and its `docs/qa-backlog/` tickets are verified
  and deleted) tags `vX.Y.Z` and publishes the Release. Production deploys pinned tags
  only. See `docs/superpowers/specs/2026-07-18-branching-strategy-design.md` for the
  full model and its rejected alternatives.
