"""Shared helpers for connectors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_ts(value: Any) -> datetime:
    """Best-effort convert a timestamp (datetime | ISO string | None) to UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            pass
    return datetime.now(UTC)
