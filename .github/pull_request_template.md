## What & why

<!-- Link the backlog ticket / issue. -->

## Checklist

- [ ] PR title is a conventional commit line (`type(scope): subject`) — it becomes the squash commit
- [ ] `ruff check . && ruff format --check .`, `mypy --strict`, `pytest` green locally
- [ ] Frontend touched? `cd web && npm run lint && npm run test && npm run build`
- [ ] Docs/PRD updated if a user-facing capability changed
- [ ] No secrets, tokens, personal data, or large binaries in the diff
