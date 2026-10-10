"""Prepare a throwaway stack's real cryptfile keyring without selecting the host's."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from alfredctl.launch import EVAL_SECRETS_PASSPHRASE

# The subprocess environment is already scoped before this import configures a backend.
_SEED_SCRIPT = """\
import json, sys
from shared.secrets import get_secret, set_secret
credentials = json.load(sys.stdin)
for integration, fields in credentials.items():
    for field, value in fields.items():
        set_secret(integration, field, value)
        if get_secret(integration, field) != value:
            raise RuntimeError("fake credential keyring readback failed")
"""


async def prepare_credentials(data_dir: Path, credentials: dict[str, dict[str, str]]) -> None:
    """Seed only fake world credentials into a newly allocated eval data directory.

    A fresh interpreter gives ``shared.secrets`` its own import-time backend. JSON on
    stdin keeps fixture values out of both the command line and shell interpolation.
    """
    if not credentials:
        return
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _SEED_SCRIPT],
        input=json.dumps(credentials),
        env={
            "ALFRED_DATA_DIR": str(data_dir.resolve()),
            "ALFRED_SECRETS_BACKEND": "cryptfile",
            "ALFRED_SECRETS_PASSPHRASE": EVAL_SECRETS_PASSPHRASE,
        },
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
