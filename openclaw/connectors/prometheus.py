"""Prometheus connector — ingests currently-firing alerts (metric → alert archetype).

Metrics are continuous numbers; the right thing to turn into discrete Signals is the
*firing alerts* (thresholds already applied by your alerting rules). Reads
`/api/v1/alerts`.
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

_SEV = {"critical": "critical", "warning": "warn", "info": "info"}


def _parse_alerts(alerts: list[dict]) -> list[Signal]:
    out: list[Signal] = []
    for a in alerts:
        if a.get("state") not in (None, "firing"):
            continue
        labels = a.get("labels", {})
        ann = a.get("annotations", {})
        out.append(
            Signal(
                source="prometheus",
                kind="alert",
                severity=_SEV.get(labels.get("severity", ""), "error"),
                ts=parse_ts(a.get("activeAt")),
                entity=labels.get("instance") or labels.get("job") or labels.get("alertname", "prometheus"),
                message=ann.get("summary") or ann.get("description") or labels.get("alertname", ""),
                raw=a,
            )
        )
    return out


@register("prometheus")
class PrometheusConnector:
    def __init__(self, settings: Settings) -> None:
        self.url = settings.prometheus_url.rstrip("/")

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.url:
            log.warning("prometheus_skipped", reason="missing PROMETHEUS_URL")
            return []
        try:
            resp = httpx.get(f"{self.url}/api/v1/alerts", timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("prometheus_fetch_failed", error=str(exc))
            return []
        alerts = resp.json().get("data", {}).get("alerts", [])
        signals = _parse_alerts(alerts)
        log.info("prometheus_fetched", count=len(signals))
        return signals
