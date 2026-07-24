"""Assemble the runtime `run` invocation for one Alfred container."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from alfredctl.runtime import Runtime, container_name, host_gateway, image_tag, trusted_subnet

if TYPE_CHECKING:
    from pathlib import Path

_GATEWAY_REWRITE_KEYS = ("OLLAMA_HOST", "LMSTUDIO_HOST", "HA_HOST", "OTEL_EXPORTER_OTLP_ENDPOINT")


@dataclass(frozen=True)
class LaunchPlan:
    run_args: list[str]
    url_hint: str
    name: str
    image: str


def _env_pairs(
    rt: Runtime,
    mode: str,
    env_file: Path | None,
    extra_env: list[str],
    passphrase: str,
) -> list[str]:
    gateway = host_gateway(rt)
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    for key in _GATEWAY_REWRITE_KEYS:
        if key in merged:
            merged[key] = merged[key].replace("localhost", gateway).replace("127.0.0.1", gateway)
    subnets = (merged.get("ALFRED_TRUSTED_NETWORKS", ""), trusted_subnet(rt))
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
) -> LaunchPlan:
    name = container_name()
    image = image_tag()
    args = ["run", "--detach", "--name", name]
    if rt.name != "container":
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
