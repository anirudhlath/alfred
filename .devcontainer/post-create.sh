#!/usr/bin/env bash
set -euo pipefail
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv venv --python 3.13
uv sync --all-extras
(cd web && npm ci)
echo "Test with: HF_HUB_OFFLINE=1 uv run pytest -q   (embedding default is HF-gated)"
