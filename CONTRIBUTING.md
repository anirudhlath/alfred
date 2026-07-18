# Contributing

Thanks for your interest in Alfred!

## License, CLA, and sign-off

Alfred is licensed under [AGPL-3.0-or-later](LICENSE). By contributing, you agree that your
contributions are licensed under the same terms.

Contributions additionally require a one-time [Contributor License Agreement](CLA.md)
signature. On your first pull request the CLA bot will prompt you; sign by replying with
the comment it shows. The CLA licenses your contribution to the project owner (you keep
ownership of it) and preserves the project's ability to be relicensed or dual-licensed.

All commits must carry a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off, certifying that you wrote the change or otherwise have the right to submit it under
the project license:

```bash
git commit -s
```

which adds a `Signed-off-by: Your Name <you@example.com>` trailer. Pull requests with unsigned
commits will be asked to rebase before merge.

## Development

```bash
uv sync --all-extras
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run pytest
```

CI runs the same checks; PRs need a green run.
