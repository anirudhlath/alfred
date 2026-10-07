from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

import httpx
import pytest

from evals.harness.preflight import PreflightError, check_home_service, check_models

if TYPE_CHECKING:
    from pathlib import Path


async def test_check_models_names_what_it_found() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": [{"id": "other-model"}]})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PreflightError) as err:
            await check_models(client, "http://localhost:8000/v1", "gemma-4-26b-a4b")
    assert "other-model" in str(err.value) and "http://localhost:8000/v1" in str(err.value)


async def test_check_models_unreachable() -> None:
    def refuse(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(PreflightError, match="not reachable"):
            await check_models(client, "http://localhost:8000/v1", "m")


def fake_git(head: str, upstream: str, dirty: str = ""):  # type: ignore[no-untyped-def]
    def git(path: Path, *args: str) -> str:
        if args[:1] == ("fetch",):
            return ""
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("rev-parse", "origin/main"):
            return upstream
        if args == ("status", "--porcelain"):
            return dirty
        raise AssertionError(args)

    return git


def test_stale_home_service_is_refused_with_the_fix(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(PreflightError, match="checkout --detach origin/main"):
        check_home_service(tmp_path, allow_stale=False, git=fake_git("aaa1111", "bbb2222"))
    assert (
        check_home_service(tmp_path, allow_stale=True, git=fake_git("aaa1111", "bbb2222"))
        == "aaa1111"
    )


def test_dirty_home_service_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(PreflightError, match="uncommitted"):
        check_home_service(tmp_path, allow_stale=False, git=fake_git("a", "a", dirty=" M app/x.py"))


def test_missing_home_service(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="not a git checkout"):
        check_home_service(tmp_path / "nope", allow_stale=False)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["model-a"]),
        httpx.Response(200, json={"data": ["model-a"]}),
        httpx.Response(200, json={"data": 3}),
    ],
    ids=["not-json", "top-level-list", "data-of-strings", "data-not-a-list"],
)
async def test_check_models_refuses_a_body_that_is_not_a_model_list(answer: httpx.Response) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: answer)) as client:
        with pytest.raises(PreflightError, match="not an OpenAI-compatible model list"):
            await check_models(client, "http://localhost:8000/v1", "m")


def test_allow_stale_still_refuses_uncommitted_changes(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(PreflightError, match="uncommitted"):
        check_home_service(tmp_path, allow_stale=True, git=fake_git("a", "b", dirty=" M app/x.py"))


def git_cli(fetch_error: Exception | None = None):  # type: ignore[no-untyped-def]
    """A fake ``subprocess.run`` for the real ``_git``: fetch fails as told, the rest answers."""

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        args = tuple(cmd[3:])
        if args[:1] == ("fetch",) and fetch_error is not None:
            raise fetch_error
        stdout = {("rev-parse", "HEAD"): "aaa1111\n", ("rev-parse", "origin/main"): "aaa1111\n"}
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout.get(args, ""), stderr="")

    return run


def _offline() -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(
        128, ["git"], output="", stderr="fatal: unable to access 'origin': could not resolve host"
    )


def test_a_failed_fetch_says_why_and_how_to_carry_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(subprocess, "run", git_cli(fetch_error=_offline()))
    with pytest.raises(PreflightError, match="could not resolve host") as err:
        check_home_service(tmp_path, allow_stale=False)
    assert "fetch -q origin main" in str(err.value)
    assert "--allow-stale-home-service" in str(err.value)


def test_a_missing_git_is_a_preflight_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    no_git = FileNotFoundError(2, "No such file or directory", "git")
    monkeypatch.setattr(subprocess, "run", git_cli(fetch_error=no_git))
    with pytest.raises(PreflightError, match="could not run"):
        check_home_service(tmp_path, allow_stale=False)


def test_allow_stale_carries_on_offline_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(subprocess, "run", git_cli(fetch_error=_offline()))
    with caplog.at_level(logging.WARNING, logger="evals.harness.preflight"):
        assert check_home_service(tmp_path, allow_stale=True) == "aaa1111"
    (warned,) = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "could not resolve host" in warned
