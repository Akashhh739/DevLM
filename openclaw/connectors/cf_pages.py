"""Cloudflare Pages connector — failed deployments and build errors.

Uses the Pages Deployments API. Requires CLOUDFLARE_API_TOKEN to have
'Account: Cloudflare Pages: Read' scope (edit the token at
dash.cloudflare.com/profile/api-tokens to add it).

Also needs CLOUDFLARE_ACCOUNT_ID (already used by cf_workers).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.connectors._util import parse_ts
from openclaw.models import Signal

log = structlog.get_logger()

_BASE = "https://api.cloudflare.com/client/v4"

_STAGE_SEV = {
    "failure": "error",
    "failed": "error",
    "canceled": "warn",
    "cancelled": "warn",
    "skipped": "warn",
}


def _parse_deployments(deployments: list[dict[str, Any]], project: str) -> list[Signal]:
    signals: list[Signal] = []
    for d in deployments:
        stage = d.get("latest_stage") or {}
        status = stage.get("status", "")
        sev = _STAGE_SEV.get(status.lower())
        if not sev:
            continue  # success / active / idle — skip

        env = d.get("environment", "production")
        stage_name = stage.get("name", "deploy")
        short_id = d.get("short_id", d.get("id", "?")[:8])
        branch = d.get("deployment_trigger", {}).get("metadata", {}).get("branch", "")
        msg = f"{project} [{env}] deployment {short_id} failed at {stage_name}"
        if branch:
            msg += f" (branch: {branch})"

        signals.append(
            Signal(
                source="cf_pages",
                kind="pages_deployment",
                severity=sev,
                ts=parse_ts(d.get("created_on")),
                entity=project,
                message=msg,
                raw=d,
            )
        )
    return signals


@register("cf_pages")
class CfPagesConnector:
    def __init__(self, settings: Settings) -> None:
        self.token = settings.cloudflare_api_token
        self.account_id = settings.cloudflare_account_id

    def fetch(self, window: timedelta) -> list[Signal]:
        if not (self.token and self.account_id):
            log.warning("cf_pages_skipped", reason="missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID")
            return []

        headers = {"Authorization": f"Bearer {self.token}"}
        since = datetime.now(UTC) - window

        try:
            r = httpx.get(
                f"{_BASE}/accounts/{self.account_id}/pages/projects",
                headers=headers, timeout=20.0,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("cf_pages_projects_failed", error=str(exc))
            return []

        data = r.json()
        if not data.get("success"):
            errors = data.get("errors", [])
            # token missing Pages scope — surface as a clear warning not a crash
            if any(e.get("code") == 10000 for e in errors):
                log.warning(
                    "cf_pages_auth_error",
                    reason="Token missing 'Account: Cloudflare Pages: Read' scope. "
                           "Edit at dash.cloudflare.com/profile/api-tokens",
                )
            else:
                log.error("cf_pages_api_error", errors=errors)
            return []

        projects = data.get("result") or []
        signals: list[Signal] = []

        for proj in projects:
            name = proj["name"]
            try:
                r2 = httpx.get(
                    f"{_BASE}/accounts/{self.account_id}/pages/projects/{name}/deployments",
                    headers=headers, timeout=20.0,
                    params={"per_page": 25},
                )
                r2.raise_for_status()
                deployments = r2.json().get("result") or []
                # only deployments within the window
                recent = [
                    d for d in deployments
                    if parse_ts(d.get("created_on")) >= since
                ]
                signals.extend(_parse_deployments(recent, name))
            except httpx.HTTPError as exc:
                log.error("cf_pages_deployments_failed", project=name, error=str(exc))

        log.info("cf_pages_fetched", count=len(signals), projects=len(projects))
        return signals
