"""Core data types shared across connectors and the snapshot writer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Signal:
    """One normalized log / event / metric row from any source.

    Every connector maps its raw payload onto this shape so the snapshot and the
    agent see a uniform schema regardless of where the data came from.
    """

    source: str  # "loki", "cloudflare", "aws", ...
    kind: str  # "log", "waf_event", "metric", ...
    severity: str  # "critical" | "error" | "warn" | "info"
    ts: datetime  # event timestamp (UTC)
    entity: str  # pod / app / service / zone the signal is about
    message: str  # human-readable line
    raw: dict[str, Any] = field(default_factory=dict)  # original payload

    def to_row(self) -> dict[str, Any]:
        """Flatten to a Parquet-friendly row (raw payload serialized to JSON)."""
        return {
            "source": self.source,
            "kind": self.kind,
            "severity": self.severity,
            "ts": self.ts,
            "entity": self.entity,
            "message": self.message,
            "raw_json": json.dumps(self.raw, default=str),
        }
