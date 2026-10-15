"""alfred prompts: push the defaults, show drift, pull Langfuse's versions back."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from alfred_cli.main import app
from shared.prompts.cli import diff_prompts, pull_prompts, push_prompts
from shared.prompts.names import SYSTEM2_LOCATION, SYSTEM2_PERSONALITY
from shared.prompts.template import repo_text
from tests.shared.prompts.fakes import FakeLangfuse

if TYPE_CHECKING:
    from pathlib import Path

P = SYSTEM2_PERSONALITY


@pytest.mark.asyncio
async def test_push_creates_a_version_only_where_the_text_differs() -> None:
    fake = FakeLangfuse({P: repo_text(P)}, version=3, labels=("latest",))
    lines = await push_prompts(fake, [P, SYSTEM2_LOCATION], promote=False)

    assert fake.created == [(SYSTEM2_LOCATION, repo_text(SYSTEM2_LOCATION), [])]
    assert lines == [f"{P}: v3 unchanged", f"{SYSTEM2_LOCATION}: pushed v4"]


@pytest.mark.asyncio
async def test_push_promote_labels_new_and_unchanged_versions_production() -> None:
    fake = FakeLangfuse({P: repo_text(P)}, version=3, labels=("candidate", "latest"))
    lines = await push_prompts(fake, [P, SYSTEM2_LOCATION], promote=True)

    assert fake.labelled == [(P, 3, ["candidate", "production"])]
    assert fake.created == [(SYSTEM2_LOCATION, repo_text(SYSTEM2_LOCATION), ["production"])]
    assert lines == [f"{P}: v3 unchanged, promoted", f"{SYSTEM2_LOCATION}: pushed v4 (production)"]


@pytest.mark.asyncio
async def test_push_promote_leaves_an_already_promoted_version_alone() -> None:
    fake = FakeLangfuse({P: repo_text(P)}, version=3, labels=("production", "latest"))
    await push_prompts(fake, [P], promote=True)
    assert (fake.created, fake.labelled) == ([], [])


@pytest.mark.asyncio
async def test_diff_reports_drift_and_unseeded_prompts() -> None:
    fake = FakeLangfuse({P: repo_text(P) + "\nBe brief."})
    lines, drifted = await diff_prompts(fake, [P, SYSTEM2_LOCATION], label="production")

    assert drifted
    assert "+Be brief." in lines
    assert f"{SYSTEM2_LOCATION}: not in Langfuse at label production" in lines


@pytest.mark.asyncio
async def test_diff_is_clean_when_langfuse_matches() -> None:
    fake = FakeLangfuse({P: repo_text(P)})
    assert await diff_prompts(fake, [P], label="production") == ([], False)


@pytest.mark.asyncio
async def test_pull_writes_langfuse_text_with_a_terminating_newline(tmp_path: Path) -> None:
    fake = FakeLangfuse({P: "You are Jeeves.", SYSTEM2_LOCATION: "In the {{room}}."})
    lines = await pull_prompts(
        fake, [P, SYSTEM2_LOCATION], label="production", defaults_dir=tmp_path
    )

    assert (tmp_path / f"{P}.md").read_text(encoding="utf-8") == "You are Jeeves.\n"
    assert not (tmp_path / f"{SYSTEM2_LOCATION}.md").exists()
    assert lines[0] == f"{P}: wrote v7"
    assert "room" in lines[1] and "kept" in lines[1]


def test_the_cli_needs_the_langfuse_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(app, ["prompts", "diff"])
    assert result.exit_code == 2
    assert "LANGFUSE_HOST" in result.output


def test_an_unknown_prompt_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    result = CliRunner().invoke(app, ["prompts", "diff", "no-such-prompt"])
    assert result.exit_code == 2
    assert "no-such-prompt" in result.output
