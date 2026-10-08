"""Unified Alfred runner — starts all core services as supervised child processes.

Usage: python -m runner [--no-reload] [--debug]

Hot-reload is enabled by default: source file changes automatically
restart the affected service.  Pass ``--no-reload`` to disable.
Pass ``--debug`` to enable verbose LiteLLM logging.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
import shutil
import sys
from pathlib import Path

from runner.supervisor import ServiceSpec, Supervisor
from shared.config import AlfredConfig, data_mode, data_path, data_root
from shared.env import is_truthy_flag
from shared.gateway import GATEWAY_REWRITE_KEYS
from shared.logging import configure_logging
from shared.otel import init_tracing

logger = logging.getLogger(__name__)

# Which env vars get rewritten lives in shared/gateway.py, one copy for both launch
# paths, so `docker compose up` with OLLAMA_HOST=localhost "just works" and means the
# same thing as `alfredctl up`.
# Docker adds host.docker.internal via extra_hosts; Podman uses host.containers.internal.
_GATEWAY_HOSTS = ("host.docker.internal", "host.containers.internal")

# Where the container path's redis-server finds its modules (ALFRED_REDIS_MODULES_DIR):
# the Containerfile copies them here from redis:8-bookworm.
_DEFAULT_REDIS_MODULES_DIR = "/usr/local/lib/redis/modules"
# Vector memory's FT.CREATE/FT.SEARCH/FT.INFO (core/memory/redis_vector_store.py) are
# RediSearch commands. A redis-server without it starts and answers PING, then fails
# every recall with "unknown command 'FT.SEARCH'" — so its absence is fatal at startup.
_REQUIRED_REDIS_MODULES = ("redisearch.so",)
# Loaded when present, as the image ships it, but nothing issues a JSON.* command (the
# context index is ON HASH) — a Redis without it is missing nothing Alfred uses.
_OPTIONAL_REDIS_MODULES = ("rejson.so",)


class RedisModulesMissingError(RuntimeError):
    """``redis-server`` would start without a module Alfred cannot run without."""


def _reachable_gateway() -> str | None:
    """First container→host gateway hostname that resolves, or None."""
    import socket

    for host in _GATEWAY_HOSTS:
        try:
            socket.gethostbyname(host)
        except OSError:
            continue
        return host
    return None


def rewrite_host_gateway(env: dict[str, str] | None = None) -> None:
    """Rewrite localhost/127.0.0.1 in host-pointing env vars to the container gateway.

    Only acts inside the managed container (ALFRED_MANAGE_INFRA set) and only if a
    gateway hostname actually resolves — a no-op for native/dev runs.
    """
    target = os.environ if env is None else env
    if not target.get("ALFRED_MANAGE_INFRA"):
        return
    gateway = _reachable_gateway()
    if gateway is None:
        return
    for key in GATEWAY_REWRITE_KEYS:
        value = target.get(key, "")
        if "localhost" in value or "127.0.0.1" in value:
            target[key] = value.replace("localhost", gateway).replace("127.0.0.1", gateway)


def build_services() -> list[ServiceSpec]:
    """Return the services the runner should supervise.

    The six core Python services are always included. Native infra
    (redis, mosquitto) and home-service are added only when
    ``ALFRED_MANAGE_INFRA`` is truthy — the container's job, not native dev.
    Raises RedisModulesMissingError when that Redis would start without RediSearch.
    """
    services = [
        ServiceSpec(name="bridge", module="bus"),
        ServiceSpec(name="reflex", module="core.reflex", delay=1.0),
        ServiceSpec(name="triggers", module="core.triggers"),
        ServiceSpec(
            name="conscious",
            module="core.conscious",
            delay=2.0,
            watch_dirs=["core/conscious/prompts"],
        ),
        ServiceSpec(
            name="channels",
            module="core.channels",
            delay=2.0,
            watch_dirs=["core/voice", "core/conscious/prompts"],
        ),
        ServiceSpec(name="memory-ingestor", module="core.memory.ingestor_main", delay=1.5),
    ]
    if is_truthy_flag(os.getenv("ALFRED_MANAGE_INFRA")):
        services = _infra_services() + services
    return services


def _infra_services() -> list[ServiceSpec]:
    async def _redis_ready() -> bool:
        import redis.asyncio as aioredis

        client = aioredis.Redis(host="localhost", port=6379, socket_timeout=2.0)
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()

    async def _mqtt_ready() -> bool:
        import asyncio as _a

        try:
            _, writer = await _a.wait_for(_a.open_connection("localhost", 1883), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    return [
        ServiceSpec(
            name="redis",
            command=_redis_command(data_root() / "redis"),
            ready_check=_redis_ready,
        ),
        ServiceSpec(
            name="mosquitto",
            command=["mosquitto", "-c", str(_write_mosquitto_conf())],
            ready_check=_mqtt_ready,
        ),
        ServiceSpec(
            name="home-service",
            command=["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"],
        ),
    ]


def _redis_command(redis_dir: Path) -> list[str]:
    """Redis argv: redis-stack-server when installed (native dev), else redis-server
    with explicit module loads (container). Persistence follows ALFRED_DATA_MODE, except
    under ALFRED_EVAL: an eval stack listens on the container interface and keeps nothing.

    Raises RedisModulesMissingError on the container path when RediSearch is absent.
    """
    redis_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("redis-stack-server"):
        return ["redis-stack-server", "--dir", str(redis_dir)]
    cmd = ["redis-server", "--dir", str(redis_dir)]
    if is_truthy_flag(os.getenv("ALFRED_EVAL")):
        # `alfred evals` drives the bus from the host through a loopback-only published
        # port, which arrives on the container's eth0; a throwaway stack keeps nothing.
        cmd += ["--bind", "0.0.0.0", "--protected-mode", "no", "--save", "", "--appendonly", "no"]
    else:
        cmd += ["--bind", "127.0.0.1"]
        if data_mode() == "persistent":
            cmd += ["--appendonly", "yes"]
        else:
            cmd += ["--save", "", "--appendonly", "no"]
    for module in _redis_modules():
        cmd += ["--loadmodule", str(module)]
    return cmd


def _redis_modules() -> list[Path]:
    """Module files for ``redis-server --loadmodule``, from ALFRED_REDIS_MODULES_DIR.

    Raises RedisModulesMissingError when a required one is absent, before any process
    exists: a modules-less Redis passes the PING readiness gate, so it would otherwise
    surface only later, as cryptic FT.* errors from memory (issue #212).
    """
    modules_dir = Path(os.getenv("ALFRED_REDIS_MODULES_DIR") or _DEFAULT_REDIS_MODULES_DIR)
    missing = [name for name in _REQUIRED_REDIS_MODULES if not (modules_dir / name).is_file()]
    if missing:
        if modules_dir.is_dir():
            present = sorted(p.name for p in modules_dir.glob("*.so"))
            found = f"found: {', '.join(present)}" if present else "it holds no .so files"
        else:
            found = "the directory does not exist"
        expected = [f"{name} (required)" for name in _REQUIRED_REDIS_MODULES] + [
            f"{name} (optional)" for name in _OPTIONAL_REDIS_MODULES
        ]
        raise RedisModulesMissingError(
            f"refusing to start redis-server without {', '.join(missing)}: "
            "redis-stack-server is not on PATH, so Redis loads its modules from "
            f"ALFRED_REDIS_MODULES_DIR={modules_dir} ({found}). Expected "
            f"{' and '.join(expected)} there — vector memory issues RediSearch FT.* "
            "commands. Rebuild the image (uv run alfredctl build), which copies them from "
            "redis:8-bookworm, or point ALFRED_REDIS_MODULES_DIR at the directory that "
            "holds them."
        )
    candidates = _REQUIRED_REDIS_MODULES + _OPTIONAL_REDIS_MODULES
    return [modules_dir / name for name in candidates if (modules_dir / name).is_file()]


def _write_mosquitto_conf() -> Path:
    """Generate a mosquitto config under the data dir (persistence per data mode)."""
    conf = data_path("mosquitto", "mosquitto.conf")
    persistence = "true" if data_mode() == "persistent" else "false"
    conf.write_text(
        "listener 1883 0.0.0.0\n"
        "allow_anonymous true\n"
        f"persistence {persistence}\n"
        f"persistence_location {conf.parent}/\n"
        "log_dest stdout\n"
    )
    _grant_broker_ownership(conf.parent)
    return conf


def _grant_broker_ownership(persistence_dir: Path) -> None:
    """Hand the persistence dir to the ``mosquitto`` user.

    Started as root, mosquitto drops privileges to ``mosquitto`` — but the runner
    creates this directory as root, so the broker cannot create ``mosquitto.db``
    inside it and every autosave fails with EACCES. No-op when the runner is
    already unprivileged (native dev) or the user does not exist.
    """
    if os.geteuid() != 0:
        return
    try:
        broker = pwd.getpwnam("mosquitto")
    except KeyError:
        logger.debug("No 'mosquitto' user — leaving %s owned by root", persistence_dir)
        return
    try:
        os.chown(persistence_dir, broker.pw_uid, broker.pw_gid)
    except OSError:
        logger.warning("Could not chown %s — MQTT persistence may fail", persistence_dir)


def main() -> None:
    reload = "--no-reload" not in sys.argv
    if "--debug" in sys.argv:
        os.environ["ALFRED_DEBUG"] = "1"
    log = configure_logging(service="runner")
    rewrite_host_gateway()  # before from_env() so config picks up the rewritten hosts
    config = AlfredConfig.from_env()

    from core.memory.paths import seed_defaults

    seed_defaults()  # first-boot: copy read-only templates into ALFRED_DATA_DIR

    init_tracing(
        service_name="runner",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )

    try:
        services = build_services()
    except RedisModulesMissingError as exc:
        # Same exit code and log channel as a failed readiness gate, but before the
        # supervisor launches anything — no Redis to start only to kill again.
        logger.error("[redis] %s", exc)
        sys.exit(1)
    names = ", ".join(s.name for s in services)
    mode = "reload" if reload else "static"
    log.info("Alfred — starting {} services ({}): {}", len(services), mode, names)

    supervisor = Supervisor(services, reload=reload, root=Path.cwd())
    code = asyncio.run(supervisor.run())
    sys.exit(code)


if __name__ == "__main__":
    main()
