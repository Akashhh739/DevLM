"""Connector registry wiring. Importing a connector module registers it."""

from __future__ import annotations

from openclaw.config import Settings
from openclaw.connectors import (  # noqa: F401  (import side effect: register)
    apprunner,
    aws,
    cf_pages,
    cf_workers,
    cloudflare,
    cloudwatch_logs,
    demo,
    dns,
    ga4,
    github,
    k8s,
    loki,
    postgres,
    prometheus,
    sentry,
    supabase,
)
from openclaw.connectors.base import REGISTRY, Connector


def build_connectors(names: list[str], settings: Settings) -> list[Connector]:
    built: list[Connector] = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown source '{name}'. Available: {sorted(REGISTRY)}")
        built.append(cls(settings))
    return built


__all__ = ["REGISTRY", "Connector", "build_connectors"]
