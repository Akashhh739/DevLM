"""Cloudflare Workers connector — per-script invocation errors (GraphQL analytics).

Account-level `workersInvocationsAdaptive`: for each Worker script, how many requests
and errors in the window. Emits a Signal for any script with errors (severity by error
rate). Reuses the Cloudflare API token; needs the account id.
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
query($acct: String!, $since: Time!, $until: Time!) {
  viewer {
    accounts(filter: {accountTag: $acct}) {
      workersInvocationsAdaptive(
        filter: {datetime_geq: $since, datetime_leq: $until},
        limit: 100,
        orderBy: [sum_requests_DESC]
      ) {
        dimensions { scriptName }
        sum { requests errors }
      }
    }
  }
}
"""


def _parse(body: dict) -> list[Signal]:
    accounts = (body.get("data") or {}).get("viewer", {}).get("accounts", [])
    rows = accounts[0]["workersInvocationsAdaptive"] if accounts else []
    out: list[Signal] = []
    for r in rows:
        totals = r.get("sum") or {}
        errors = totals.get("errors", 0)
        if not errors:
            continue
        requests = totals.get("requests", 0)
        script = (r.get("dimensions") or {}).get("scriptName", "worker")
        rate = errors / requests if requests else 1.0
        out.append(
            Signal(
                source="cf_workers",
                kind="worker_errors",
                severity="error" if rate >= 0.05 else "warn",
                ts=datetime.now(UTC),
                entity=script,
                message=f"{script}: {errors} errors / {requests} requests ({rate:.1%})",
                raw=r,
            )
        )
    return out


@register("cf_workers")
class CloudflareWorkersConnector:
    def __init__(self, settings: Settings) -> None:
        self.token = settings.cloudflare_api_token
        self.account = settings.cloudflare_account_id

    def fetch(self, window: timedelta) -> list[Signal]:
        if not (self.token and self.account):
            log.warning("cf_workers_skipped", reason="missing CLOUDFLARE_API_TOKEN/ACCOUNT_ID")
            return []
        until = datetime.now(UTC)
        since = until - window
        payload = {
            "query": _QUERY,
            "variables": {
                "acct": self.account,
                "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        }
        try:
            resp = httpx.post(
                _GQL_URL, json=payload,
                headers={"Authorization": f"Bearer {self.token}"}, timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("cf_workers_fetch_failed", error=str(exc))
            return []
        signals = _parse(resp.json())
        log.info("cf_workers_fetched", count=len(signals))
        return signals
