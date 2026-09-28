"""Demo connector — synthetic signals so the pipeline runs with no external services.

Useful for local development, tests, and demos of the snapshot → analyze flow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

_SAMPLE = [
    ("critical", "auth-svc", "OOMKilled: container exceeded memory limit 512Mi"),
    ("error", "billing", "Traceback: NoneType has no attribute 'id' at billing/charge.py:42"),
    ("error", "gateway", "502 Bad Gateway upstream auth-svc connect timeout"),
    ("warn", "gateway", "rate-limit near threshold for client 203.0.113.9"),
    ("error", "billing", "Traceback: NoneType has no attribute 'id' at billing/charge.py:42"),
]


@register("demo")
class DemoConnector:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(self, window: timedelta) -> list[Signal]:
        now = datetime.now(UTC)
        return [
            Signal(
                source="demo",
                kind="log",
                severity=sev,
                ts=now - timedelta(minutes=5 * i),
                entity=entity,
                message=msg,
                raw={"synthetic": True},
            )
            for i, (sev, entity, msg) in enumerate(_SAMPLE)
        ]
