"""runner.__main__.build_services respects ALFRED_MANAGE_INFRA."""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from runner.__main__ import (
    RedisModulesMissingError,
    _redis_command,
    _write_mosquitto_conf,
    build_services,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def redis_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An ALFRED_REDIS_MODULES_DIR holding both modules the image ships."""
    modules = tmp_path / "mods"
    modules.mkdir()
    for name in ("redisearch.so", "rejson.so"):
        (modules / name).touch()
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(modules))
    return modules


def test_core_only_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALFRED_MANAGE_INFRA", raising=False)
    names = {s.name for s in build_services()}
    assert names == {"bridge", "reflex", "triggers", "conscious", "channels", "memory-ingestor"}


def test_infra_added_when_flag_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_modules: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_MANAGE_INFRA", "1")
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    names = {s.name for s in build_services()}
    assert {"redis", "mosquitto", "home-service"}.issubset(names)
    # redis/mosquitto are native-command services with readiness checks:
    by_name = {s.name: s for s in build_services()}
    assert by_name["redis"].command is not None
    assert by_name["redis"].ready_check is not None


def test_redis_command_container_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_modules: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "persistent")
    monkeypatch.delenv("ALFRED_EVAL", raising=False)
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[0] == "redis-server"
    assert "--appendonly" in cmd and cmd[cmd.index("--appendonly") + 1] == "yes"
    loaded = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--loadmodule"]
    assert loaded == [str(redis_modules / "redisearch.so"), str(redis_modules / "rejson.so")]
    assert "--bind" in cmd


def test_redis_command_starts_with_redisearch_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_modules: Path
) -> None:
    """RedisJSON is loaded when present, but nothing issues a JSON.* command."""
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    (redis_modules / "rejson.so").unlink()
    cmd = _redis_command(tmp_path / "redis")
    loaded = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--loadmodule"]
    assert loaded == [str(redis_modules / "redisearch.so")]


def test_redis_command_refuses_an_empty_modules_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Issue #212: a misbuilt image must not boot a Redis that answers FT.* with errors."""
    modules = tmp_path / "mods"
    modules.mkdir()
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(modules))
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    with pytest.raises(RedisModulesMissingError) as refused:
        _redis_command(tmp_path / "redis")
    message = str(refused.value)
    assert str(modules) in message
    assert "redisearch.so" in message
    assert "rejson.so" in message
    assert "ALFRED_REDIS_MODULES_DIR" in message


def test_redis_command_refuses_a_missing_modules_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    modules = tmp_path / "absent"
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(modules))
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    with pytest.raises(RedisModulesMissingError, match="does not exist"):
        _redis_command(tmp_path / "redis")


def test_redis_command_refuses_rejson_without_redisearch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_modules: Path
) -> None:
    """The module vector memory needs is the one that must be there."""
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    (redis_modules / "redisearch.so").unlink()
    with pytest.raises(RedisModulesMissingError, match=r"found: rejson\.so\)"):
        _redis_command(tmp_path / "redis")


def test_redis_command_ephemeral_disables_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_modules: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "ephemeral")
    monkeypatch.delenv("ALFRED_EVAL", raising=False)
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[cmd.index("--appendonly") + 1] == "no"
    assert cmd[cmd.index("--bind") + 1] == "127.0.0.1"


def test_redis_command_eval_binds_all_interfaces_without_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_modules: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "persistent")
    monkeypatch.setenv("ALFRED_EVAL", "1")
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[cmd.index("--bind") + 1] == "0.0.0.0"
    assert cmd[cmd.index("--protected-mode") + 1] == "no"
    assert cmd[cmd.index("--save") + 1] == ""
    assert cmd[cmd.index("--appendonly") + 1] == "no"


def test_redis_command_prefers_stack_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Native dev: redis-stack-server loads its own modules, so no modules dir is needed."""
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: "/opt/redis-stack-server")
    assert _redis_command(tmp_path / "redis") == [
        "redis-stack-server",
        "--dir",
        str(tmp_path / "redis"),
    ]


def test_runner_exits_nonzero_before_starting_anything_without_redisearch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The runner refuses at startup, naming the dir — no Redis is launched and killed."""
    modules = tmp_path / "mods"
    modules.mkdir()
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_MANAGE_INFRA", "1")
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(modules))
    monkeypatch.setattr(sys, "argv", ["runner", "--no-reload"])
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    monkeypatch.setattr("runner.__main__._reachable_gateway", lambda: None)
    # The real configure_logging swaps the process-wide loguru sinks out from under
    # every later test; tracing and seeding are side effects this test does not need.
    monkeypatch.setattr("runner.__main__.configure_logging", lambda service: loguru_logger)
    monkeypatch.setattr("runner.__main__.init_tracing", lambda **_: None)
    monkeypatch.setattr("core.memory.paths.seed_defaults", lambda: None)

    def no_supervisor(*_: object, **__: object) -> None:
        raise AssertionError("the supervisor must not start without RediSearch")

    monkeypatch.setattr("runner.__main__.Supervisor", no_supervisor)
    caplog.set_level(logging.ERROR, logger="runner.__main__")

    with pytest.raises(SystemExit) as exited:
        main()

    assert exited.value.code == 1
    errors = [
        r.getMessage()
        for r in caplog.records
        if r.name == "runner.__main__" and r.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    assert errors[0].startswith("[redis] ")
    assert str(modules) in errors[0]
    assert "redisearch.so" in errors[0]


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
