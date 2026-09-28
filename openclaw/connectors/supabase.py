"""Supabase connector — auth audit log (failed logins, suspicious auth events).

Supabase runs on Postgres and records auth activity in `auth.audit_log_entries`.
Surfacing those is a Supabase-specific security signal (distinct from generic DB
health). Read-only; needs the service DB connection string. psycopg is lazy ([postgres]).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from openclaw.config import Settings
from openclaw.connectors._util import parse_ts
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_BAD = ("fail", "error", "denied", "invalid", "revoked")


def _parse_audit(rows: list[dict]) -> list[Signal]:
    out: list[Signal] = []
    for row in rows:
        action = str(row.get("action", "")).lower()
        severity = "warn" if any(k in action for k in _BAD) else "info"
        out.append(
            Signal(
                source="supabase",
                kind="auth_audit",
                severity=severity,
                ts=parse_ts(row.get("created_at")),
                entity=row.get("actor") or "auth",
                message=f"{row.get('action', '')} {row.get('actor', '')}".strip(),
                raw=row,
            )
        )
    return out


@register("supabase")
class SupabaseConnector:
    def __init__(self, settings: Settings) -> None:
        self.dsn = settings.supabase_db_url

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.dsn:
            log.warning("supabase_skipped", reason="missing SUPABASE_DB_URL")
            return []
        try:
            import psycopg
        except ImportError:
            log.error("supabase_skipped", reason='pip install "psycopg[binary]"')
            return []

        since = datetime.now(UTC) - window
        try:
            with psycopg.connect(self.dsn, connect_timeout=10) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT payload->>'action' AS action, "
                    "payload->>'actor_username' AS actor, created_at "
                    "FROM auth.audit_log_entries WHERE created_at > %s "
                    "ORDER BY created_at DESC LIMIT 500",
                    (since,),
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 - a down/absent table shouldn't crash the run
            log.error("supabase_fetch_failed", error=str(exc))
            return []

        signals = _parse_audit(rows)
        log.info("supabase_fetched", count=len(signals))
        return signals
