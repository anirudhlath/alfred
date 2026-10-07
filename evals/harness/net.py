"""Where the host-side fakes listen, and how the eval container names them."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from alfredctl.runtime import DOCKER_HOST_ALIAS
from evals.harness._proc import run_checked
from evals.harness.preflight import PreflightError

IN_CONTAINER_HOST = DOCKER_HOST_ALIAS
# Fixed, and below the Linux ephemeral range (32768-60999), so a host firewall rule can
# name them (docs/evals.md#host-firewall). `alfred evals run --fake-ha-port/--proxy-port`
# moves them; 0 takes any free port.
DEFAULT_FAKE_HA_PORT = 18123
DEFAULT_PROXY_PORT = 18100
HOST_FIREWALL_DOC = "docs/evals.md#host-firewall"
# Names a host-side URL uses for the host itself; inside the container they mean the container.
_HOST_SELF_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def docker_bridge_gateway() -> str:
    """The default bridge's gateway IP: reachable from the container, not from the LAN."""
    gateway = run_checked(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ],
        timeout=30,
        error=PreflightError,
    ).strip()
    if not gateway:
        raise PreflightError("docker's bridge network has no gateway — is docker running?")
    return gateway


def in_container_url(port: int) -> str:
    return f"http://{IN_CONTAINER_HOST}:{port}"


def container_reachable(url: str) -> str:
    """A host-side URL as the container must spell it: a host that names the host itself
    becomes its in-container name, and any other URL is left exactly as it is."""
    parts = urlsplit(url)
    if parts.hostname not in _HOST_SELF_NAMES:
        return url
    try:
        port = parts.port
    except ValueError as exc:  # out of range, or not a number
        raise PreflightError(f"{url!r} has an invalid port ({exc})") from exc
    netloc = IN_CONTAINER_HOST if port is None else f"{IN_CONTAINER_HOST}:{port}"
    userinfo, at, _ = parts.netloc.rpartition("@")
    return urlunsplit(parts._replace(netloc=f"{userinfo}{at}{netloc}"))
