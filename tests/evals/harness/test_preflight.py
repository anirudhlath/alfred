from __future__ import annotations

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
