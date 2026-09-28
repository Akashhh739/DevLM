"""AWS App Runner connector — service status (event-like) via boto3.

Lists App Runner services and flags any not in RUNNING (failed create/delete, paused,
or an in-progress operation). Read-only; reuses AWS creds + region ([aws] extra).
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from openclaw.config import Settings
from openclaw.connectors._util import parse_ts
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_STATUS = {
    "CREATE_FAILED": "error",
    "DELETE_FAILED": "error",
    "PAUSED": "warn",
    "OPERATION_IN_PROGRESS": "warn",
}


def _parse_services(services: list[dict]) -> list[Signal]:
    out: list[Signal] = []
    for s in services:
        status = s.get("Status", "")
        if status == "RUNNING":
            continue
        out.append(
            Signal(
                source="apprunner",
                kind="service_status",
                severity=_STATUS.get(status, "warn"),
                ts=parse_ts(s.get("UpdatedAt")),
                entity=s.get("ServiceName", "apprunner"),
                message=f"{s.get('ServiceName', '')} status={status}",
                raw=s,
            )
        )
    return out


@register("apprunner")
class AppRunnerConnector:
    def __init__(self, settings: Settings) -> None:
        self.region = settings.aws_region

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.region:
            log.warning("apprunner_skipped", reason="missing AWS_REGION")
            return []
        try:
            import boto3
        except ImportError:
            log.error("apprunner_skipped", reason="pip install boto3")
            return []
        try:
            client = boto3.client("apprunner", region_name=self.region)
            services = client.list_services().get("ServiceSummaryList", [])
        except Exception as exc:  # noqa: BLE001 - one source failing shouldn't kill the run
            log.error("apprunner_fetch_failed", error=str(exc))
            return []
        signals = _parse_services(services)
        log.info("apprunner_fetched", count=len(signals))
        return signals
