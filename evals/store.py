"""Save and load EvalRun results as JSON files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.models import EvalRun

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


def build_run_id(timestamp: datetime, model: str) -> str:
    """Build a filesystem-safe run ID from timestamp and model name."""
    ts_str = timestamp.strftime("%Y-%m-%dT%H%M%S")
    safe_model = model.replace(":", "-")
    return f"{ts_str}_{safe_model}"


def save_run(run: EvalRun, runs_dir: Path) -> Path:
    """Save an EvalRun as a JSON file. Returns the file path."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.run_id}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(run_id: str, runs_dir: Path) -> EvalRun:
    """Load an EvalRun from a JSON file by run_id."""
    path = runs_dir / f"{run_id}.json"
    return EvalRun.model_validate_json(path.read_text())


def list_runs(runs_dir: Path) -> list[str]:
    """List all run IDs in the runs directory, sorted by name."""
    if not runs_dir.exists():
        return []
    return sorted(p.stem for p in runs_dir.glob("*.json"))
