"""Persist the agent's Markdown report alongside its snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def save_report(run_dir: Path, report: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    path = run_dir / "report.md"
    path.write_text(f"# OpenClaw report — {stamp}\n\n{report}\n")
    return path
