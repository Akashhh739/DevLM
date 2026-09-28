"""CloudWatch Logs connector — raw log lines from App Runner (and any configured group).

Auto-discovers all /aws/apprunner/*/application log groups when AWS_REGION is set.
Additional groups can be added via CLOUDWATCH_LOG_GROUPS (comma-separated).
Filters for error/warning/exception patterns; each matching line becomes a Signal.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

# Patterns that make a log line worth surfacing
_ERROR_RE = re.compile(
    r"(error|exception|traceback|critical|fatal|warn|failed|timeout|refused|5\d\d\s)",
    re.IGNORECASE,
)

_LEVEL_MAP = {
    "critical": "critical",
    "fatal": "critical",
    "error": "error",
    "exception": "error",
    "traceback": "error",
    "warn": "warn",
    "warning": "warn",
    "failed": "warn",
    "timeout": "warn",
    "refused": "warn",
}


def _severity(line: str) -> str:
    low = line.lower()
    for keyword, sev in _LEVEL_MAP.items():
        if keyword in low:
            return sev
    return "warn"


def _entity_from_group(group_name: str) -> str:
    """Extract a short service name from the log group path."""
    # /aws/apprunner/strukt-server-mumbai/<id>/application → strukt-server-mumbai
    parts = group_name.strip("/").split("/")
    if len(parts) >= 3 and parts[1] == "apprunner":
        return parts[2]
    return parts[-1]


def _parse_events(events: list[dict[str, Any]], group_name: str) -> list[Signal]:
    entity = _entity_from_group(group_name)
    signals: list[Signal] = []
    for ev in events:
        message = ev.get("message", "").rstrip()
        if not _ERROR_RE.search(message):
            continue
        ts_ms = ev.get("timestamp", 0)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC) if ts_ms else datetime.now(UTC)
        signals.append(
            Signal(
                source="cloudwatch_logs",
                kind="log_line",
                severity=_severity(message),
                ts=ts,
                entity=entity,
                message=message[:500],
                raw={"group": group_name, "stream": ev.get("logStreamName", ""), "message": message},
            )
        )
    return signals


@register("cloudwatch_logs")
class CloudWatchLogsConnector:
    def __init__(self, settings: Settings) -> None:
        self.region = settings.aws_region
        self.extra_groups = [
            g.strip() for g in settings.cloudwatch_log_groups.split(",") if g.strip()
        ]

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.region:
            log.warning("cloudwatch_logs_skipped", reason="missing AWS_REGION")
            return []
        try:
            import boto3
        except ImportError:
            log.error("cloudwatch_logs_skipped", reason='pip install -e ".[aws]"')
            return []

        client = boto3.client("logs", region_name=self.region)
        groups = self._discover_groups(client)
        if not groups:
            log.info("cloudwatch_logs_no_groups")
            return []

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - int(window.total_seconds() * 1000)

        signals: list[Signal] = []
        for group in groups:
            try:
                paginator = client.get_paginator("filter_log_events")
                for page in paginator.paginate(
                    logGroupName=group,
                    startTime=start_ms,
                    endTime=end_ms,
                    limit=500,
                ):
                    signals.extend(_parse_events(page.get("events", []), group))
            except Exception as exc:  # noqa: BLE001
                log.error("cloudwatch_logs_group_failed", group=group, error=str(exc))

        log.info("cloudwatch_logs_fetched", count=len(signals), groups=len(groups))
        return signals

    def _discover_groups(self, client: Any) -> list[str]:
        """Auto-discover /aws/apprunner/*/application groups + any extra configured ones."""
        groups: list[str] = []
        try:
            paginator = client.get_paginator("describe_log_groups")
            for page in paginator.paginate(logGroupNamePrefix="/aws/apprunner"):
                for g in page.get("logGroups", []):
                    name = g["logGroupName"]
                    # only application logs (not service/infra logs)
                    if name.endswith("/application"):
                        groups.append(name)
        except Exception as exc:  # noqa: BLE001
            log.error("cloudwatch_logs_discover_failed", error=str(exc))
        groups.extend(g for g in self.extra_groups if g not in groups)
        return groups
