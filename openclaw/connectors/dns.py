"""DNS connector — resolves configured hostnames and flags failures.

A point-in-time check (window is ignored): each host in DNS_HOSTS is resolved;
failures become error signals, successes info signals with the resolved addresses.
Uses the stdlib resolver — no extra dependency.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

Resolver = Callable[[str], list[str]]


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return sorted({str(info[4][0]) for info in infos})


def _check(hosts: list[str], resolver: Resolver) -> list[Signal]:
    now = datetime.now(UTC)
    signals: list[Signal] = []
    for host in hosts:
        try:
            addrs = resolver(host)
            signals.append(
                Signal(
                    source="dns",
                    kind="dns_resolution",
                    severity="info",
                    ts=now,
                    entity=host,
                    message=f"{host} -> {', '.join(addrs)}",
                    raw={"host": host, "addresses": addrs},
                )
            )
        except Exception as exc:  # noqa: BLE001 - resolution failure is the signal
            signals.append(
                Signal(
                    source="dns",
                    kind="dns_resolution",
                    severity="error",
                    ts=now,
                    entity=host,
                    message=f"{host} failed to resolve: {exc}",
                    raw={"host": host, "error": str(exc)},
                )
            )
    return signals


@register("dns")
class DNSConnector:
    def __init__(self, settings: Settings) -> None:
        self.hosts = [h.strip() for h in settings.dns_hosts.split(",") if h.strip()]

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.hosts:
            log.warning("dns_skipped", reason="no DNS_HOSTS configured")
            return []
        signals = _check(self.hosts, _default_resolver)
        log.info("dns_checked", count=len(signals))
        return signals
