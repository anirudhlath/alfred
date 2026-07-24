"""runner.__main__.build_services respects ALFRED_MANAGE_INFRA."""

from __future__ import annotations

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
