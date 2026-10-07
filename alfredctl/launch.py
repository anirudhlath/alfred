"""Assemble the runtime `run` invocation for one Alfred container."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from alfredctl.runtime import (
    Runtime,
    container_name,
    eval_container_name,
    host_gateway,
    image_tag,
    trusted_subnet,
)
from shared.env import is_truthy_flag
from shared.gateway import GATEWAY_REWRITE_KEYS

if TYPE_CHECKING:
    from pathlib import Path

# `alfred evals` stacks: throwaway, driven from the host, never holding a real secret.
EVAL_SECRETS_PASSPHRASE = "alfred-eval-not-a-secret"
# Unknown models are priced at the default rate (core/conscious/cost.py), so a local
# model under eval would trip the $5 default cap mid-run.
EVAL_ENV: dict[str, str] = {"ALFRED_EVAL": "1", "DAILY_COST_CAP_USD": "1000000"}


@dataclass(frozen=True)
class LaunchPlan:
    run_args: list[str]
    url_hint: str
    name: str
    image: str
    # Lines for the operator about decisions that are invisible in `run_args` — a
    # withheld container subnet looks identical to one that was never wanted.
    notes: tuple[str, ...] = ()


def _env_pairs(
    rt: Runtime,
    mode: str,
    env_file: Path | None,
    extra_env: list[str],
    passphrase: str,
    defaults: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Build the ``-e KEY=VALUE`` run args. Returns (pairs, operator notes).

    *defaults* override the env file, mode and passphrase; ``--env`` still overrides them.
    """
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    # Gateway rewriting stays scoped to env-file values, which is what it has always
    # done. `--env OLLAMA_HOST=http://localhost:11434` arguably deserves the same
    # rewrite, but widening it here would also make `host_gateway()` shell out for a
    # key the file never mentioned; that is a separate change with its own blast radius.
    if any(key in merged for key in GATEWAY_REWRITE_KEYS):
        gateway = host_gateway(rt)
        for key in GATEWAY_REWRITE_KEYS:
            if key in merged:
                merged[key] = (
                    merged[key].replace("localhost", gateway).replace("127.0.0.1", gateway)
                )
    merged["ALFRED_DATA_MODE"] = mode
    merged["ALFRED_SECRETS_PASSPHRASE"] = passphrase
    merged.update(defaults or {})
    if os.getenv("HF_TOKEN"):
        merged.setdefault("HF_TOKEN", os.environ["HF_TOKEN"])
    # `--env` is applied before the trusted-networks block, not after, so both of that
    # block's inputs reach it by the same route. Applied after, `--env
    # ALFRED_TRUSTED_NETWORKS=...` silently discarded the auto-appended container subnet
    # while `--env ALFRED_TRUSTED_NETWORKS_STRICT=...` needed a bespoke rescan to be seen
    # at all. Everything else keeps its previous precedence: `--env` still wins over the
    # env file and over --mode/--passphrase, because those are set above it.
    for item in extra_env:
        key, _, value = item.partition("=")
        merged[key] = value
    notes: list[str] = []
    # The container subnet is auto-trusted so the SPA works out of the box from the
    # host — but strict mode means "trust only what I listed", and appending it anyway
    # would silently re-trust every peer on that network, including a reverse proxy
    # fronting the internet. Strict mode only drops the *built-in* LAN defaults on the
    # server side, so this list is the one place the subnet can be withheld.
    if is_truthy_flag(merged.get("ALFRED_TRUSTED_NETWORKS_STRICT")):
        subnet = ""
        notes.append(
            f"ALFRED_TRUSTED_NETWORKS_STRICT set: not adding container subnet "
            f"{trusted_subnet(rt)} — list your LAN CIDRs explicitly. A browser on this "
            f"host will now reach Alfred as the bridge gateway and be refused; register "
            f"passkeys from a listed LAN CIDR or over Tailscale instead."
        )
    else:
        subnet = trusted_subnet(rt)
    subnets = (merged.get("ALFRED_TRUSTED_NETWORKS", ""), subnet)
    merged["ALFRED_TRUSTED_NETWORKS"] = ",".join(x for x in subnets if x)
    pairs: list[str] = []
    for key, value in merged.items():
        pairs += ["-e", f"{key}={value}"]
    return pairs, notes


def build_plan(
    rt: Runtime,
    *,
    mode: str,
    persist: Path | None,
    models: Path,
    hf_cache: Path | None,
    expose_ha: bool,
    expose_home: bool,
    port: int,
    extra_env: list[str],
    env_file: Path | None,
    passphrase: str,
    memory: str = "8g",
    cpus: int = 4,
    eval_mode: bool = False,
) -> LaunchPlan:
    if eval_mode:
        if rt.name == "container":
            raise ValueError("--eval supports docker and podman")
        if persist is None:
            raise ValueError("--eval needs a persist dir")
        if env_file is not None:
            # The operator's .env holds real secrets; an eval stack must never see them.
            raise ValueError("--eval never reads an env file")
        if expose_ha or expose_home:
            # Both publish on every interface: a no-secret stack open to the LAN, on host
            # ports the deployed stack may already hold.
            raise ValueError("--eval publishes loopback-only ports; drop --expose-ha/--expose-home")
    name = eval_container_name() if eval_mode else container_name()
    image = image_tag()
    args = ["run", "--detach", "--name", name]
    if rt.name == "container":
        # Apple container VMs default to 2 GB / few CPUs — far below what the
        # full stack (torch + whisper + embeddings + redis) needs; the VM OOMs
        # and stops silently. Docker/Podman size their VM/host limits themselves.
        args += ["--memory", memory, "--cpus", str(cpus)]
    else:
        if eval_mode:
            # Loopback-only, host-chosen ports: never clashes with a running stack on
            # 8081, and the harness reaches Redis without exposing it to the LAN.
            args += ["-p", "127.0.0.1::8081", "-p", "127.0.0.1::6379"]
        else:
            args += ["-p", f"{port}:8081"]
        if expose_ha:
            args += ["-p", "1883:1883"]
        if expose_home:
            args += ["-p", "8000:8000"]
        if rt.name == "docker" and sys.platform == "linux":
            args += ["--add-host", "host.docker.internal:host-gateway"]
    args += ["-v", f"{models}:/models"]
    if hf_cache is not None:
        args += ["-v", f"{hf_cache}:/models/hf"]
    if (mode == "persistent" or eval_mode) and persist is not None:
        args += ["-v", f"{persist}:/data"]
    env_args, notes = _env_pairs(
        rt, mode, env_file, extra_env, passphrase, EVAL_ENV if eval_mode else None
    )
    args += env_args
    args += [image]
    if rt.name == "container":
        url = "resolve-ip"
    elif eval_mode:
        # The host port is chosen at start; `up` asks the runtime which one it got.
        url = "resolve-port"
    else:
        url = f"http://localhost:{port}"
    return LaunchPlan(run_args=args, url_hint=url, name=name, image=image, notes=tuple(notes))
