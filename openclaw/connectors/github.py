"""GitHub connector — recent CI workflow-run failures (event-like).

Lets the agent correlate "broke right after this deploy/CI run". Reads the Actions
`runs` API; keeps only non-successful runs.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import structlog

from openclaw.config import Settings
from openclaw.connectors._util import parse_ts
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_CONCLUSION = {
    "failure": "error",
    "timed_out": "error",
    "startup_failure": "error",
    "cancelled": "warn",
    "action_required": "warn",
}


def _parse_runs(runs: list[dict]) -> list[Signal]:
    out: list[Signal] = []
    for r in runs:
        concl = r.get("conclusion")
        if concl in (None, "success", "skipped", "neutral"):
            continue
        out.append(
            Signal(
                source="github",
                kind="ci_run",
                severity=_CONCLUSION.get(concl, "warn"),
                ts=parse_ts(r.get("updated_at")),
                entity=r.get("name") or r.get("head_branch") or "github",
                message=f"{r.get('name', '')} #{r.get('run_number', '')} {concl} "
                f"on {r.get('head_branch', '')}",
                raw=r,
            )
        )
    return out


@register("github")
class GitHubConnector:
    def __init__(self, settings: Settings) -> None:
        self.token = settings.github_token
        self.repo = settings.github_repo

    def fetch(self, window: timedelta) -> list[Signal]:
        if not (self.token and self.repo):
            log.warning("github_skipped", reason="missing GITHUB_TOKEN/GITHUB_REPO")
            return []
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{self.repo}/actions/runs",
                params={"per_page": 50},
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("github_fetch_failed", error=str(exc))
            return []
        runs = resp.json().get("workflow_runs", [])
        signals = _parse_runs(runs)
        log.info("github_fetched", count=len(signals))
        return signals
