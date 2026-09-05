from __future__ import annotations

from pathlib import Path

import pytest

from alfredctl import launch
from alfredctl import runtime as runtime_module
from alfredctl.launch import LaunchPlan, build_plan
from alfredctl.runtime import Runtime

DOCKER = Runtime("docker", "docker")
PODMAN = Runtime("podman", "podman")
APPLE = Runtime("container", "container")


@pytest.fixture(autouse=True)
def _no_apple_gateway_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """host_gateway() must only shell out (Apple `container network inspect`) when a
    gateway-rewrite env key is actually present in the merged env — never unconditionally.

    Note: build_plan()/container_name() legitimately shell out to `git branch
    --show-current` for naming on every call — that's pre-existing, unrelated to this
    guard, and intentionally left alone here.
    """

    def _boom(rt: Runtime) -> str:
        raise AssertionError("host_gateway() shelled out to the Apple runtime unexpectedly")

    monkeypatch.setattr(runtime_module, "_apple_vmnet_gateway", _boom)


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


def test_docker_linux_adds_add_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch.sys, "platform", "linux")
    args = _plan(rt=DOCKER).run_args
    assert "--add-host" in args
    assert args[args.index("--add-host") + 1] == "host.docker.internal:host-gateway"


def test_hf_token_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "tok123")
    args = _plan().run_args
    assert "HF_TOKEN=tok123" in args


def test_apple_gateway_lookup_only_triggered_when_key_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lazy-lookup half of the guard: with a gateway-rewrite key present, the Apple
    vmnet lookup DOES get invoked (and its result is used for the rewrite)."""
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_HOST=http://localhost:11434\n")
    monkeypatch.setattr(runtime_module, "_apple_vmnet_gateway", lambda rt: "192.168.64.9")
    args = _plan(rt=APPLE, env_file=env_file).run_args
    assert "OLLAMA_HOST=http://192.168.64.9:11434" in args


def test_trusted_networks_merge(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALFRED_TRUSTED_NETWORKS=10.1.0.0/16\n")
    args = _plan(env_file=env_file).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    values = entry.split("=", 1)[1].split(",")
    assert "10.1.0.0/16" in values
    assert "172.16.0.0/12" in values


def test_strict_mode_from_env_file_skips_container_subnet(tmp_path: Path) -> None:
    """Strict mode means "trust only what I listed" — auto-appending the container
    subnet would silently re-trust every peer on the Docker network, including a
    reverse proxy fronting the internet."""
    env_file = tmp_path / ".env"
    env_file.write_text("ALFRED_TRUSTED_NETWORKS=10.9.0.7\nALFRED_TRUSTED_NETWORKS_STRICT=1\n")
    args = _plan(env_file=env_file).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    values = entry.split("=", 1)[1].split(",")
    assert "172.16.0.0/12" not in values
    assert values == ["10.9.0.7"]


def test_strict_mode_from_extra_env_skips_container_subnet(tmp_path: Path) -> None:
    """--env ALFRED_TRUSTED_NETWORKS_STRICT=1 must be honoured too, not just the flag
    in the env file."""
    env_file = tmp_path / ".env"
    env_file.write_text("ALFRED_TRUSTED_NETWORKS=10.9.0.7\n")
    args = _plan(env_file=env_file, extra_env=["ALFRED_TRUSTED_NETWORKS_STRICT=true"]).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    values = entry.split("=", 1)[1].split(",")
    assert "172.16.0.0/12" not in values
    assert values == ["10.9.0.7"]


def test_extra_env_overrides_env_file_strict_flag(tmp_path: Path) -> None:
    """--env wins over the env file, not the other way round: an operator turning the
    flag off for one run must actually get the container subnet back."""
    env_file = tmp_path / ".env"
    env_file.write_text("ALFRED_TRUSTED_NETWORKS_STRICT=1\n")
    args = _plan(env_file=env_file, extra_env=["ALFRED_TRUSTED_NETWORKS_STRICT=0"]).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    assert "172.16.0.0/12" in entry.split("=", 1)[1].split(",")


def test_extra_env_trusted_networks_keeps_container_subnet(tmp_path: Path) -> None:
    """--env ALFRED_TRUSTED_NETWORKS=... must merge with the auto-appended subnet the
    same way an env-file value does. Applied after the merge it silently replaced it,
    so `alfredctl up --env ALFRED_TRUSTED_NETWORKS=...` broke host access."""
    args = _plan(extra_env=["ALFRED_TRUSTED_NETWORKS=10.9.0.0/16"]).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    values = entry.split("=", 1)[1].split(",")
    assert values == ["10.9.0.0/16", "172.16.0.0/12"]


@pytest.mark.parametrize("flag", ["1", "true", "YES"])
def test_strict_mode_truthy_spellings(tmp_path: Path, flag: str) -> None:
    """Truthiness must match _strict_networks() in core/channels/web_server.py, or
    alfredctl and the server disagree about what strict mode means. Both now route
    through shared.env.is_truthy_flag."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"ALFRED_TRUSTED_NETWORKS_STRICT={flag}\n")
    args = _plan(env_file=env_file).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    assert "172.16.0.0/12" not in entry


@pytest.mark.parametrize("flag", [" 1 ", " Yes "])
def test_strict_mode_tolerates_whitespace_on_the_env_flag_path(flag: str) -> None:
    """Whitespace has to be stripped, and --env is the only route that can carry it:
    python-dotenv already strips env-file values, so an env-file case cannot fail if
    the strip() is deleted."""
    args = _plan(extra_env=[f"ALFRED_TRUSTED_NETWORKS_STRICT={flag}"]).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    assert "172.16.0.0/12" not in entry


def test_strict_mode_warns_that_the_subnet_was_withheld(tmp_path: Path) -> None:
    """Withholding the subnet is invisible in run_args and costs the operator host
    access to passkey registration — say so rather than let them discover it as a 403."""
    env_file = tmp_path / ".env"
    env_file.write_text("ALFRED_TRUSTED_NETWORKS_STRICT=1\n")
    notes = _plan(env_file=env_file).notes
    assert len(notes) == 1
    assert "ALFRED_TRUSTED_NETWORKS_STRICT" in notes[0]
    assert "172.16.0.0/12" in notes[0]


@pytest.mark.parametrize(
    ("rt_", "subnet"),
    [
        (DOCKER, "172.16.0.0/12"),
        (PODMAN, "10.88.0.0/16"),
        (APPLE, "192.168.64.0/24"),
    ],
)
def test_strict_mode_note_names_the_active_runtimes_subnet(
    tmp_path: Path, rt_: Runtime, subnet: str
) -> None:
    """The note tells the operator which CIDR was withheld, so it has to come from
    trusted_subnet(rt) rather than the Docker value everything else is tested with —
    a Podman or Apple operator reading "172.16.0.0/12" would go looking for a bridge
    that does not exist on their host. (The autouse Apple-gateway guard also holds
    here: no gateway-rewrite key is present, so nothing may shell out.)"""
    env_file = tmp_path / ".env"
    env_file.write_text("ALFRED_TRUSTED_NETWORKS_STRICT=1\n")
    notes = _plan(rt=rt_, env_file=env_file).notes
    assert len(notes) == 1
    assert subnet in notes[0]


def test_strict_mode_with_extra_env_networks_lists_exactly_those(tmp_path: Path) -> None:
    """The note's own recommended recipe — strict mode plus an explicit LAN CIDR —
    must produce that CIDR and nothing else. Combining the two paths is where an
    ordering slip would show up: `--env ALFRED_TRUSTED_NETWORKS` used to be applied
    after the subnet merge, and the auto-append is skipped by a separate branch."""
    args = _plan(
        extra_env=[
            "ALFRED_TRUSTED_NETWORKS_STRICT=1",
            "ALFRED_TRUSTED_NETWORKS=10.9.0.0/16",
        ]
    ).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    assert entry.split("=", 1)[1].split(",") == ["10.9.0.0/16"]


def test_no_note_when_the_subnet_is_added() -> None:
    assert _plan().notes == ()


@pytest.mark.parametrize("flag", ["0", "", "false", "no"])
def test_non_strict_still_appends_container_subnet(tmp_path: Path, flag: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"ALFRED_TRUSTED_NETWORKS_STRICT={flag}\n")
    args = _plan(env_file=env_file).run_args
    entry = next(a for a in args if a.startswith("ALFRED_TRUSTED_NETWORKS="))
    assert "172.16.0.0/12" in entry.split("=", 1)[1].split(",")


def test_apple_container_gets_memory_and_cpus() -> None:
    args = _plan(rt=APPLE).run_args
    assert "--memory" in args and args[args.index("--memory") + 1] == "8g"
    assert "--cpus" in args and args[args.index("--cpus") + 1] == "4"


def test_docker_gets_no_vm_sizing_flags() -> None:
    args = _plan().run_args
    assert "--memory" not in args and "--cpus" not in args


def test_openai_compat_host_rewritten_to_gateway(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_COMPAT_HOST=http://localhost:8000\n")
    args = _plan(env_file=env_file).run_args
    assert "OPENAI_COMPAT_HOST=http://host.docker.internal:8000" in args
