"""Grafana Loki connector — pulls error-ish log lines for a time window."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_ERROR_RE = r"(?i)(error|exception|failed|fatal|panic|crash)"
_CRIT_RE = re.compile(r"(?i)(fatal|panic|crash|oom)")
_WARN_RE = re.compile(r"(?i)(warn|deprecat)")


def _severity(line: str) -> str:
    if _CRIT_RE.search(line):
        return "critical"
    if _WARN_RE.search(line):
        return "warn"
    return "error"  # the LogQL filter already only matches error-ish lines


@register("loki")
class LokiConnector:
    def __init__(self, settings: Settings) -> None:
        self.url = settings.loki_url.rstrip("/")
        self.namespace = settings.loki_namespace

    def _query(self) -> str:
        selector = 'app!="loki", app!="promtail"'
        if self.namespace:
            selector = f'namespace="{self.namespace}", {selector}'
        return f'{{{selector}}} |~ "{_ERROR_RE}"'

    def fetch(self, window: timedelta) -> list[Signal]:
        end_ns = time.time_ns()
        start_ns = end_ns - int(window.total_seconds() * 1e9)
        params: dict[str, str | int] = {
            "query": self._query(),
            "start": start_ns,
            "end": end_ns,
            "limit": 1000,
        }
        try:
            resp = httpx.get(f"{self.url}/loki/api/v1/query_range", params=params, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("loki_fetch_failed", error=str(exc))
            return []

        results = resp.json().get("data", {}).get("result", [])
        signals: list[Signal] = []
        for stream in results:
            labels = stream.get("stream", {})
            entity = labels.get("app") or labels.get("pod") or labels.get("container") or "unknown"
            for ts_ns, line in stream.get("values", []):
                signals.append(
                    Signal(
                        source="loki",
                        kind="log",
                        severity=_severity(line),
                        ts=datetime.fromtimestamp(int(ts_ns) / 1e9, tz=UTC),
                        entity=entity,
                        message=line,
                        raw={"labels": labels},
                    )
                )
        log.info("loki_fetched", count=len(signals))
        return signals
