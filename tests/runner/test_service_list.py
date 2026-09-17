"""runner.__main__.build_services respects ALFRED_MANAGE_INFRA."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from runner.__main__ import _redis_command, _write_mosquitto_conf, build_services

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_core_only_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALFRED_MANAGE_INFRA", raising=False)
    names = {s.name for s in build_services()}
    assert names == {"bridge", "reflex", "triggers", "conscious", "channels", "memory-ingestor"}


def test_infra_added_when_flag_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_MANAGE_INFRA", "1")
    names = {s.name for s in build_services()}
    assert {"redis", "mosquitto", "home-service"}.issubset(names)
    # redis/mosquitto are native-command services with readiness checks:
    by_name = {s.name: s for s in build_services()}
    assert by_name["redis"].command is not None
    assert by_name["redis"].ready_check is not None


def test_redis_command_container_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "persistent")
    modules = tmp_path / "mods"
    modules.mkdir()
    (modules / "redisearch.so").touch()
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(modules))
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[0] == "redis-server"
    assert "--appendonly" in cmd and cmd[cmd.index("--appendonly") + 1] == "yes"
    assert str(modules / "redisearch.so") in cmd
    assert "--bind" in cmd


def test_redis_command_ephemeral_disables_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "ephemeral")
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[cmd.index("--appendonly") + 1] == "no"


def test_redis_command_prefers_stack_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: "/opt/redis-stack-server")
    assert _redis_command(tmp_path / "redis")[0] == "redis-stack-server"


def test_mosquitto_conf_generated_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "ephemeral")
    conf = _write_mosquitto_conf()
    assert conf == tmp_path / "mosquitto" / "mosquitto.conf"
    text = conf.read_text()
    assert "listener 1883" in text
    assert "persistence false" in text


def _record_chowns(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    """Capture os.chown calls made by the runner instead of performing them."""
    calls: list[tuple[str, int, int]] = []

    def fake_chown(path: int | str | Path, uid: int, gid: int) -> None:
        calls.append((str(path), uid, gid))

    monkeypatch.setattr("runner.__main__.os.chown", fake_chown)
    return calls


def test_mosquitto_dir_handed_to_broker_user_when_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mosquitto drops privileges, so the root-created dir must become its own."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("runner.__main__.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "runner.__main__.pwd.getpwnam",
        lambda _: SimpleNamespace(pw_uid=100, pw_gid=101),
    )
    calls = _record_chowns(monkeypatch)
    conf = _write_mosquitto_conf()
    assert calls == [(str(conf.parent), 100, 101)]


def test_mosquitto_dir_not_chowned_when_not_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Native dev runs unprivileged — the broker already owns what it creates."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("runner.__main__.os.geteuid", lambda: 1000)
    calls = _record_chowns(monkeypatch)
    _write_mosquitto_conf()
    assert calls == []


def test_mosquitto_chown_skipped_when_user_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A root host without a `mosquitto` user must still boot."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("runner.__main__.os.geteuid", lambda: 0)

    def raise_keyerror(_: str) -> SimpleNamespace:
        raise KeyError("mosquitto")

    monkeypatch.setattr("runner.__main__.pwd.getpwnam", raise_keyerror)
    calls = _record_chowns(monkeypatch)
    assert _write_mosquitto_conf().exists()
    assert calls == []


def test_mosquitto_chown_failure_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A chown refusal degrades to today's behaviour rather than killing the runner."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("runner.__main__.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "runner.__main__.pwd.getpwnam",
        lambda _: SimpleNamespace(pw_uid=100, pw_gid=101),
    )

    def raise_oserror(*_: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr("runner.__main__.os.chown", raise_oserror)
    assert _write_mosquitto_conf().exists()
