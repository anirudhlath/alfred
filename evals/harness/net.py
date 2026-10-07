"""Where the host-side fakes listen, and how the eval container names them."""

from __future__ import annotations

import subprocess

IN_CONTAINER_HOST = "host.docker.internal"


def docker_bridge_gateway() -> str:
    """The default bridge's gateway IP: reachable from the container, not from the LAN."""
    out = subprocess.run(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    gateway = out.stdout.strip()
    if not gateway:
        raise RuntimeError("docker's bridge network has no gateway — is docker running?")
    return gateway


def in_container_url(port: int) -> str:
    return f"http://{IN_CONTAINER_HOST}:{port}"


def container_reachable(url: str) -> str:
    """A host-side URL as the container must spell it."""
    return url.replace("localhost", IN_CONTAINER_HOST).replace("127.0.0.1", IN_CONTAINER_HOST)
