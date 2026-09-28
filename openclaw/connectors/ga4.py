"""Google Analytics 4 connector — daily traffic for the window (baseline context).

Gives the agent a traffic trend to correlate against (e.g. is a WAF surge matching
a real traffic spike?). Uses the GA4 Data API via a read-only service account
(GOOGLE_APPLICATION_CREDENTIALS); the google client is imported lazily.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()


def _parse(rows: list[dict[str, Any]]) -> list[Signal]:
    """rows: [{"date": "20260927", "sessions": "1234", "activeUsers": "567"}, ...]"""
    signals: list[Signal] = []
    for r in rows:
        signals.append(
            Signal(
                source="ga4",
                kind="traffic",
                severity="info",
                ts=_ts(r.get("date", "")),
                entity="site",
                message=f"{r.get('date', '')}: sessions={r.get('sessions', '?')} "
                f"users={r.get('activeUsers', '?')}",
                raw=r,
            )
        )
    return signals


@register("ga4")
class GA4Connector:
    def __init__(self, settings: Settings) -> None:
        self.property_id = settings.ga4_property_id

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.property_id:
            log.warning("ga4_skipped", reason="missing GA4_PROPERTY_ID")
            return []
        try:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
            from google.analytics.data_v1beta.types import (
                DateRange,
                Dimension,
                Metric,
                RunReportRequest,
            )
        except ImportError:
            log.error("ga4_skipped", reason="pip install google-analytics-data")
            return []

        days = max(1, int(window.total_seconds() // 86400))
        try:
            client = BetaAnalyticsDataClient()
            resp = client.run_report(
                RunReportRequest(
                    property=f"properties/{self.property_id}",
                    dimensions=[Dimension(name="date")],
                    metrics=[Metric(name="sessions"), Metric(name="activeUsers")],
                    date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
                )
            )
        except Exception as exc:  # noqa: BLE001 - surface as no data, don't crash the run
            log.error("ga4_fetch_failed", error=str(exc))
            return []

        rows = [
            {
                "date": row.dimension_values[0].value,
                "sessions": row.metric_values[0].value,
                "activeUsers": row.metric_values[1].value,
            }
            for row in resp.rows
        ]
        signals = _parse(rows)
        log.info("ga4_fetched", count=len(signals))
        return signals


def _ts(yyyymmdd: str) -> datetime:
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)
