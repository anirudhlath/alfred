"""Container runtime detection and per-runtime knowledge (gateways, subnets, naming)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Runtime:
    name: str  # docker | container | podman
    exe: str


_DETECT_ORDER_DARWIN = ("container", "docker", "podman")
_DETECT_ORDER_OTHER = ("docker", "podman")


def detect(preferred: str | None) -> Runtime:
    """Resolve the container runtime: explicit choice, else platform preference order."""
    if preferred:
        if preferred not in ("docker", "container", "podman"):
            raise RuntimeError(
                f"Unknown runtime {preferred!r} (expected docker | container | podman)"
            )
        exe = shutil.which(preferred)
        if exe is None:
            raise RuntimeError(f"Requested runtime {preferred!r} not found on PATH")
        return Runtime(preferred, exe)
    order = _DETECT_ORDER_DARWIN if sys.platform == "darwin" else _DETECT_ORDER_OTHER
    for name in order:
        exe = shutil.which(name)
        if exe is not None:
            return Runtime(name, exe)
    raise RuntimeError("No container runtime found. Install Docker, Apple container, or Podman.")


def _current_branch() -> str:
    out = subprocess.run(
        ["git", "branch", "--show-current"], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def branch_slug() -> str:
    branch = _current_branch() or "detached"
    slug = re.sub(r"[^a-z0-9-]+", "-", branch.lower()).strip("-")
    return slug[:40] or "detached"


def image_tag() -> str:
    return f"alfred:{branch_slug()}"


def container_name() -> str:
    return f"alfred-{branch_slug()}"


def host_gateway(rt: Runtime) -> str:
    """Address at which the container reaches the HOST (for Ollama/LM Studio/HA)."""
    if rt.name == "docker":
        return "host.docker.internal"
    if rt.name == "podman":
        return "host.containers.internal"
    return _apple_vmnet_gateway(rt)


def _apple_vmnet_gateway(rt: Runtime) -> str:
    try:
        out = subprocess.run(
            [rt.exe, "network", "inspect", "default"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(out.stdout)
        entry = payload[0] if isinstance(payload, list) else payload
        subnet = str(entry.get("subnet", ""))
        if subnet:
            base = subnet.split("/")[0].rsplit(".", 1)[0]
            return f"{base}.1"
    except Exception:
        pass
    return "192.168.64.1"


def trusted_subnet(rt: Runtime) -> str:
    """Container-side source subnet to add to ALFRED_TRUSTED_NETWORKS."""
    return {
        "docker": "172.16.0.0/12",
        "podman": "10.88.0.0/16",
        "container": "192.168.64.0/24",
    }[rt.name]
