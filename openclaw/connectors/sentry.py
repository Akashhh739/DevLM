"""Sentry connector — unresolved issues for a window via the Issues API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_LEVEL = {"fatal": "critical", "error": "error", "warning": "warn", "info": "info"}


def _parse(issues: list[dict[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for it in issues:
        signals.append(
            Signal(
                source="sentry",
                kind="sentry_issue",
                severity=_LEVEL.get(it.get("level", ""), "error"),
                ts=_ts(it.get("lastSeen")),
                entity=it.get("culprit") or it.get("project", {}).get("slug", "sentry"),
                message=f"{it.get('title', '')} (events={it.get('count', '?')})",
                raw=it,
            )
        )
    return signals


def _stats_period(window: timedelta) -> str:
    # Sentry issues API only accepts '24h' or '14d'
    return "24h" if window.total_seconds() <= 86400 else "14d"


@register("sentry")
class SentryConnector:
    def __init__(self, settings: Settings) -> None:
        self.url = settings.sentry_url.rstrip("/")
        self.token = settings.sentry_token
        self.org = settings.sentry_org
        # SENTRY_PROJECT accepts a single slug or comma-separated list
        self.projects = [p.strip() for p in settings.sentry_project.split(",") if p.strip()]

    def fetch(self, window: timedelta) -> list[Signal]:
        if not (self.token and self.org and self.projects):
            log.warning("sentry_skipped", reason="missing SENTRY_TOKEN/ORG/PROJECT")
            return []
        signals: list[Signal] = []
        for project in self.projects:
            signals.extend(self._fetch_project(project, window))
        log.info("sentry_fetched", count=len(signals), projects=self.projects)
        return signals

    def _fetch_project(self, project: str, window: timedelta) -> list[Signal]:
        endpoint = f"{self.url}/api/0/projects/{self.org}/{project}/issues/"
        params: dict[str, str | int] = {
            "query": "is:unresolved",
            "statsPeriod": _stats_period(window),
            "limit": 100,
        }
        try:
            resp = httpx.get(
                endpoint,
                params=params,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("sentry_fetch_failed", project=project, error=str(exc))
            return []
        return _parse(resp.json())


def _ts(value: str | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)
