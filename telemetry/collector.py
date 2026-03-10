"""Telemetry collector — drains metrics from the telemetry buffer to research vault CSVs.

Runs as a background task. Reads from the in-memory telemetry buffer (or Redis Stream
in production) and appends to CSV files + generates daily Markdown summaries.
"""

from __future__ import annotations

import contextlib
import csv
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def flush_to_csv(entries: list[dict[str, Any]], vault_path: str) -> None:
    """Append telemetry entries to the appropriate CSV files in the research vault."""
    data_dir = Path(vault_path) / "data"

    for entry in entries:
        category = entry.get("category", entry.get("metric_type", "general"))
        category_dir = data_dir / str(category)
        category_dir.mkdir(parents=True, exist_ok=True)

        csv_path = category_dir / "raw.csv"
        file_exists = csv_path.exists()

        fieldnames = sorted(entry.keys())
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(entry)


def generate_daily_summary(vault_path: str, date: str | None = None) -> str:
    """Generate a daily Markdown summary from today's telemetry data."""
    if date is None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")

    data_dir = Path(vault_path) / "data"
    if not data_dir.exists():
        return ""

    lines = [f"# Daily Research Note — {date}\n"]

    for category_dir in sorted(data_dir.iterdir()):
        if not category_dir.is_dir():
            continue

        csv_path = category_dir / "raw.csv"
        if not csv_path.exists():
            continue

        values: list[float] = []
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("timestamp", "").startswith(date) and "value" in row:
                    with contextlib.suppress(ValueError):
                        values.append(float(row["value"]))

        if values:
            lines.append(f"\n## {category_dir.name}")
            lines.append(f"- Count: {len(values)}")
            lines.append(f"- Mean: {statistics.mean(values):.1f}")
            lines.append(f"- Median (p50): {statistics.median(values):.1f}")
            if len(values) >= 2:
                sorted_vals = sorted(values)
                p95_idx = int(len(sorted_vals) * 0.95)
                p99_idx = int(len(sorted_vals) * 0.99)
                lines.append(f"- p95: {sorted_vals[min(p95_idx, len(sorted_vals) - 1)]:.1f}")
                lines.append(f"- p99: {sorted_vals[min(p99_idx, len(sorted_vals) - 1)]:.1f}")

    summary = "\n".join(lines)

    daily_dir = Path(vault_path) / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_path = daily_dir / f"{date}.md"
    with open(daily_path, "w") as f:
        f.write(summary)

    return summary
