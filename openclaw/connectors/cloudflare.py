"""Cloudflare connector — pulls WAF / firewall events for a window.

Surfaces IP-spam and attack activity via the GraphQL Analytics API
(`firewallEventsAdaptive`). Needs a read-only token (Analytics:Read,
Firewall Services:Read) and a zone id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_GQL_URL = "https://api.cloudflare.com/client/v4/graphql"
_QUERY = """
query($zone: String!, $since: Time!, $until: Time!) {
  viewer {
    zones(filter: {zoneTag: $zone}) {
      firewallEventsAdaptive(
        filter: {datetime_geq: $since, datetime_leq: $until},
        limit: 1000,
        orderBy: [datetime_DESC]
      ) {
        action
        clientIP
        clientRequestHTTPHost
        clientRequestPath
        source
        ruleId
        datetime
      }
    }
  }
}
"""

# WAF action -> our severity
_SEVERITY = {
    "block": "error",
    "drop": "error",
    "managed_challenge": "warn",
    "challenge": "warn",
    "jschallenge": "warn",
}


@register("cloudflare")
class CloudflareConnector:
    def __init__(self, settings: Settings) -> None:
        self.token = settings.cloudflare_api_token
        self.zone = settings.cloudflare_zone_id

    def fetch(self, window: timedelta) -> list[Signal]:
        if not (self.token and self.zone):
            log.warning("cloudflare_skipped", reason="missing CLOUDFLARE_API_TOKEN/ZONE_ID")
            return []

        until = datetime.now(UTC)
        since = until - window
        payload = {
            "query": _QUERY,
            "variables": {
                "zone": self.zone,
                "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        }
        try:
            resp = httpx.post(
                _GQL_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("cloudflare_fetch_failed", error=str(exc))
            return []

        return _parse(resp.json())


def _parse(body: dict) -> list[Signal]:
    zones = (body.get("data") or {}).get("viewer", {}).get("zones", [])
    events = zones[0]["firewallEventsAdaptive"] if zones else []
    signals: list[Signal] = []
    for ev in events:
        action = ev.get("action", "")
        host = ev.get("clientRequestHTTPHost", "")
        path = ev.get("clientRequestPath", "")
        signals.append(
            Signal(
                source="cloudflare",
                kind="waf_event",
                severity=_SEVERITY.get(action, "info"),
                ts=_parse_ts(ev.get("datetime")),
                entity=ev.get("clientIP", "unknown"),
                message=f"{action} rule={ev.get('ruleId', '')} {host}{path} "
                f"src={ev.get('source', '')}",
                raw=ev,
            )
        )
    log.info("cloudflare_fetched", count=len(signals))
    return signals


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value)
