"""AWS connector — GuardDuty findings + CloudWatch alarms in ALARM state.

Read-only. Credentials come from the standard AWS chain (env / profile / role);
region from AWS_REGION. boto3 is imported lazily so the package installs without it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()


def _gd_severity(score: float) -> str:
    if score >= 7:
        return "critical"
    if score >= 4:
        return "error"
    return "warn"


def _parse_guardduty(findings: list[dict[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for f in findings:
        rtype = f.get("Resource", {}).get("ResourceType", "")
        signals.append(
            Signal(
                source="aws",
                kind="guardduty_finding",
                severity=_gd_severity(float(f.get("Severity", 0))),
                ts=_ts(f.get("UpdatedAt") or f.get("CreatedAt")),
                entity=f.get("Region", rtype) or "aws",
                message=f"{f.get('Type', '')}: {f.get('Title', '')}",
                raw=f,
            )
        )
    return signals


def _parse_alarms(alarms: list[dict[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for a in alarms:
        signals.append(
            Signal(
                source="aws",
                kind="cloudwatch_alarm",
                severity="error",
                ts=_ts(a.get("StateUpdatedTimestamp")),
                entity=a.get("Namespace", a.get("AlarmName", "")) or "cloudwatch",
                message=f"ALARM {a.get('AlarmName', '')}: {a.get('StateReason', '')}",
                raw=a,
            )
        )
    return signals


@register("aws")
class AWSConnector:
    def __init__(self, settings: Settings) -> None:
        self.region = settings.aws_region

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.region:
            log.warning("aws_skipped", reason="missing AWS_REGION")
            return []
        try:
            import boto3  # lazy: only needed when actually fetching
        except ImportError:
            log.error("aws_skipped", reason="boto3 not installed (pip install boto3)")
            return []

        signals: list[Signal] = []
        try:
            signals += self._guardduty(boto3, window)
        except Exception as exc:  # noqa: BLE001 - one source failing shouldn't kill the run
            log.error("aws_guardduty_failed", error=str(exc))
        try:
            signals += self._alarms(boto3)
        except Exception as exc:  # noqa: BLE001
            log.error("aws_alarms_failed", error=str(exc))
        log.info("aws_fetched", count=len(signals))
        return signals

    def _guardduty(self, boto3: Any, window: timedelta) -> list[Signal]:
        gd = boto3.client("guardduty", region_name=self.region)
        detectors = gd.list_detectors().get("DetectorIds", [])
        out: list[dict[str, Any]] = []
        for det in detectors:
            ids = gd.list_findings(DetectorId=det).get("FindingIds", [])
            if ids:
                out += gd.get_findings(DetectorId=det, FindingIds=ids[:50]).get("Findings", [])
        return _parse_guardduty(out)

    def _alarms(self, boto3: Any) -> list[Signal]:
        cw = boto3.client("cloudwatch", region_name=self.region)
        alarms = cw.describe_alarms(StateValue="ALARM").get("MetricAlarms", [])
        return _parse_alarms(alarms)


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)
