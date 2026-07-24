from __future__ import annotations

from pathlib import Path

from alfredctl.launch import LaunchPlan, build_plan
from alfredctl.runtime import Runtime

DOCKER = Runtime("docker", "docker")
APPLE = Runtime("container", "container")


def _plan(
    rt: Runtime = DOCKER,
    *,
    mode: str = "ephemeral",
    persist: Path | None = None,
    models: Path = Path("/m"),
    hf_cache: Path | None = None,
    expose_ha: bool = False,
    expose_home: bool = False,
    port: int = 8081,
    extra_env: list[str] | None = None,
    env_file: Path | None = None,
    passphrase: str = "pp",
) -> LaunchPlan:
    return build_plan(
        rt,
        mode=mode,
        persist=persist,
        models=models,
        hf_cache=hf_cache,
        expose_ha=expose_ha,
        expose_home=expose_home,
        port=port,
        extra_env=extra_env if extra_env is not None else [],
        env_file=env_file,
        passphrase=passphrase,
    )


def test_only_8081_published_by_default() -> None:
    args = _plan().run_args
    assert args.count("-p") == 1
    assert "8081:8081" in args
    assert not any("6379" in a for a in args)


def test_apple_container_publishes_nothing() -> None:
    assert "-p" not in _plan(rt=APPLE).run_args


def test_expose_flags_add_ports() -> None:
    args = _plan(expose_ha=True, expose_home=True).run_args
    assert "1883:1883" in args and "8000:8000" in args


def test_persistent_mounts_data(tmp_path: Path) -> None:
    args = _plan(mode="persistent", persist=tmp_path).run_args
    assert f"{tmp_path}:/data" in args


def test_ephemeral_does_not_mount_data() -> None:
    args = _plan().run_args
    assert not any(a.endswith(":/data") for a in args)


def test_models_always_mounted() -> None:
    args = _plan().run_args
    assert any(a.endswith(":/models") for a in args)


def test_localhost_rewritten_to_gateway(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_HOST=http://localhost:11434\nHA_TOKEN=abc\n")
    args = _plan(env_file=env_file).run_args
    assert "OLLAMA_HOST=http://host.docker.internal:11434" in args
    assert "HA_TOKEN=abc" in args


def test_trusted_subnet_injected() -> None:
    args = _plan().run_args
    assert any(a.startswith("ALFRED_TRUSTED_NETWORKS=") and "172.16.0.0/12" in a for a in args)


def test_mode_and_passphrase_set() -> None:
    args = _plan(mode="seed").run_args
    assert "ALFRED_DATA_MODE=seed" in args
    assert "ALFRED_SECRETS_PASSPHRASE=pp" in args
