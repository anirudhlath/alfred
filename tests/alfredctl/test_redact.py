from __future__ import annotations

import pytest

from alfredctl.redact import REDACTED, redact_command, redact_userinfo


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("http://user:pw@vllm.example:8001", "http://***@vllm.example:8001"),
        ("https://tok@embed.example/v1", "https://***@embed.example/v1"),
        # A password containing @: httpx reads the *last* @ as the delimiter, so
        # stopping at the first one leaves the tail of the password on screen.
        ("http://user:p@ss@vllm.example:8001", "http://***@vllm.example:8001"),
        # No scheme at all: httpx rejects it, but doctor still prints it in the
        # UnsupportedProtocol detail, so it has to be redacted before it gets there.
        ("user:pw@vllm.example:8001", "***@vllm.example:8001"),
        ("//user:pw@vllm.example:8001", "//***@vllm.example:8001"),
        # No userinfo, and an @ that belongs to the path: all left alone.
        ("http://vllm.example:8001", "http://vllm.example:8001"),
        ("http://vllm.example/models/a@b", "http://vllm.example/models/a@b"),
        ("vllm.example:8001/models/p@th", "vllm.example:8001/models/p@th"),
        ("", ""),
    ],
)
def test_redact_userinfo(raw: str, shown: str) -> None:
    assert redact_userinfo(raw) == shown


@pytest.mark.parametrize(
    ("cmd", "shown"),
    [
        # Every value goes, whatever the key: a name list would miss the next variable.
        (["run", "-e", "OPENROUTER_API_KEY=sk-1"], ["run", "-e", "OPENROUTER_API_KEY=***"]),
        (["run", "-e", "ALFRED_DATA_MODE=seed"], ["run", "-e", "ALFRED_DATA_MODE=***"]),
        (["run", "--env", "HA_TOKEN=t"], ["run", "--env", "HA_TOKEN=***"]),
        (["run", "--env=HA_TOKEN=t"], ["run", "--env=HA_TOKEN=***"]),
        # Only the first = splits key from value; the rest is value.
        (["run", "-e", "K=a=b"], ["run", "-e", "K=***"]),
        (["run", "-e", "K="], ["run", "-e", "K=***"]),
        # A URL value is elided whole, not just its userinfo.
        (["run", "-e", "HA_HOST=http://u:pw@ha:8123"], ["run", "-e", "HA_HOST=***"]),
        # `-e KEY` passes the host's value through; nothing is on the line to hide.
        (["run", "-e", "HF_TOKEN", "img"], ["run", "-e", "HF_TOKEN", "img"]),
        # A trailing flag with nothing after it is not an error worth raising over.
        (["run", "-e"], ["run", "-e"]),
        # Outside env pairs, URLs keep everything but their userinfo.
        (["build", "https://u:pw@git.example/a.git"], ["build", "https://***@git.example/a.git"]),
    ],
)
def test_redact_command(cmd: list[str], shown: list[str]) -> None:
    assert redact_command(cmd) == shown


def test_redact_command_leaves_everything_else_alone() -> None:
    """The echoed line must still say what ran: flags, ports, volumes, name, image."""
    cmd = [
        "docker",
        "run",
        "--detach",
        "--name",
        "alfred-fix-x",
        "-p",
        "8081:8081",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-v",
        "/home/me/.cache/alfred/models:/models",
        "alfred:fix-x",
    ]
    assert redact_command(cmd) == cmd


def test_redact_command_returns_a_copy() -> None:
    """Display only: the list that runs must keep its values."""
    cmd = ["docker", "run", "-e", "HA_TOKEN=t", "https://u:pw@h"]
    before = list(cmd)
    shown = redact_command(cmd)
    assert cmd == before
    assert shown is not cmd
    assert shown[3] == f"HA_TOKEN={REDACTED}"
