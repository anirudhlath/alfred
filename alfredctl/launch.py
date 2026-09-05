"""Assemble the runtime `run` invocation for one Alfred container."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from alfredctl.runtime import Runtime, container_name, host_gateway, image_tag, trusted_subnet
from shared.gateway import GATEWAY_REWRITE_KEYS

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class LaunchPlan:
    run_args: list[str]
    url_hint: str
    name: str
    image: str


def _strict_trusted_networks(merged: dict[str, str], extra_env: list[str]) -> bool:
    """True when ALFRED_TRUSTED_NETWORKS_STRICT is set to a truthy value.

    Truthiness mirrors ``_strict_networks()`` in ``core/channels/web_server.py`` — if the
    two disagree, alfredctl and the server disagree about what "strict" means. ``--env``
    items are checked as well as the env file because ``extra_env`` is applied after the
    merge, so a flag passed on the command line would otherwise be invisible here.
    """
    value = merged.get("ALFRED_TRUSTED_NETWORKS_STRICT", "")
    for item in extra_env:
        key, _, raw = item.partition("=")
        if key == "ALFRED_TRUSTED_NETWORKS_STRICT":
            value = raw
    return value.strip().lower() in ("1", "true", "yes")


def _env_pairs(
    rt: Runtime,
    mode: str,
    env_file: Path | None,
    extra_env: list[str],
    passphrase: str,
) -> list[str]:
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    if any(key in merged for key in GATEWAY_REWRITE_KEYS):
        gateway = host_gateway(rt)
        for key in GATEWAY_REWRITE_KEYS:
            if key in merged:
                merged[key] = (
                    merged[key].replace("localhost", gateway).replace("127.0.0.1", gateway)
                )
    # The container subnet is auto-trusted so the SPA works out of the box from the
    # host — but strict mode means "trust only what I listed", and appending it anyway
    # would silently re-trust every peer on that network, including a reverse proxy
    # fronting the internet. Strict mode only drops the *built-in* LAN defaults on the
    # server side, so this list is the one place the subnet can be withheld.
    subnet = "" if _strict_trusted_networks(merged, extra_env) else trusted_subnet(rt)
    subnets = (merged.get("ALFRED_TRUSTED_NETWORKS", ""), subnet)
    trusted = ",".join(x for x in subnets if x)
    merged["ALFRED_TRUSTED_NETWORKS"] = trusted
    merged["ALFRED_DATA_MODE"] = mode
    merged["ALFRED_SECRETS_PASSPHRASE"] = passphrase
    if os.getenv("HF_TOKEN"):
        merged.setdefault("HF_TOKEN", os.environ["HF_TOKEN"])
    for item in extra_env:
        key, _, value = item.partition("=")
        merged[key] = value
    pairs: list[str] = []
    for key, value in merged.items():
        pairs += ["-e", f"{key}={value}"]
    return pairs


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
) -> LaunchPlan:
    name = container_name()
    image = image_tag()
    args = ["run", "--detach", "--name", name]
    if rt.name == "container":
        # Apple container VMs default to 2 GB / few CPUs — far below what the
        # full stack (torch + whisper + embeddings + redis) needs; the VM OOMs
        # and stops silently. Docker/Podman size their VM/host limits themselves.
        args += ["--memory", memory, "--cpus", str(cpus)]
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
    if mode == "persistent" and persist is not None:
        args += ["-v", f"{persist}:/data"]
    args += _env_pairs(rt, mode, env_file, extra_env, passphrase)
    args += [image]
    url = "resolve-ip" if rt.name == "container" else f"http://localhost:{port}"
    return LaunchPlan(run_args=args, url_hint=url, name=name, image=image)
