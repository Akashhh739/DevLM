"""Shared run pipeline: snapshot the sources, then (optionally) analyze.

Used by both the CLI (`openclaw run`) and the dashboard API so behavior is identical.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog

from openclaw.config import Settings
from openclaw.connectors import build_connectors
from openclaw.snapshot import new_run_dir, write_snapshot

log = structlog.get_logger()


def execute(
    settings: Settings,
    window: timedelta,
    sources: list[str],
    snapshot_only: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    connectors = build_connectors(sources, settings)
    run_dir = new_run_dir(settings.snapshots_dir)

    counts: dict[str, int] = {}
    for conn in connectors:
        signals = conn.fetch(window)
        write_snapshot(signals, run_dir, conn.name)
        counts[conn.name] = len(signals)

    result: dict[str, Any] = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "counts": counts,
        "total": sum(counts.values()),
        "report": None,
    }
    if snapshot_only:
        return result

    if not (settings.openai_api_key and settings.openai_model):
        result["note"] = "Set OPENAI_API_KEY and OPENAI_MODEL to analyze."
        return result

    from openclaw.analyze import run_analysis  # lazy: only when analyzing

    result["report"] = run_analysis(run_dir, settings, apply=apply)
    return result
