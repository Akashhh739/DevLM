"""PostgreSQL health connector — the metric/threshold archetype.

Unlike log/event sources, a database gives you *numbers* (connection counts, cache
hit ratio, deadlocks). A number isn't good or bad until you compare it to a
**threshold** — that comparison is what turns a metric into a Signal.

Read-only: runs a few diagnostic SELECTs against pg_stat_* views. Needs a read-only
connection string (DATABASE_URL). psycopg is imported lazily (optional extra).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from openclaw.config import Settings
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

# Thresholds — the heart of this archetype. Tune to your DB / constraints.yaml.
CONN_WARN = 0.80  # fraction of max_connections in use
CONN_ERROR = 0.95
LONG_QUERY_WARN = 1  # count of queries running > 30s
LONG_QUERY_ERROR = 5
CACHE_HIT_WARN = 0.90  # below this, too much disk reading


def _evaluate(stats: dict[str, float | int | None]) -> list[Signal]:
    """Compare measured DB metrics to thresholds and emit a Signal per problem."""
    now = datetime.now(UTC)
    out: list[Signal] = []

    def add(severity: str, message: str) -> None:
        out.append(
            Signal(
                source="postgres",
                kind="db_health",
                severity=severity,
                ts=now,
                entity="database",
                message=message,
                raw=dict(stats),
            )
        )

    used, mx = stats.get("connections_used"), stats.get("connections_max")
    if used is not None and mx:
        ratio = used / mx
        if ratio >= CONN_ERROR:
            add("error", f"connection saturation {used}/{mx} ({ratio:.0%})")
        elif ratio >= CONN_WARN:
            add("warn", f"connections high {used}/{mx} ({ratio:.0%})")

    long_q = stats.get("long_queries")
    if long_q:
        sev = "error" if long_q >= LONG_QUERY_ERROR else "warn"
        add(sev, f"{long_q} queries running longer than 30s")

    deadlocks = stats.get("deadlocks")
    if deadlocks:
        add("warn", f"{deadlocks} deadlocks recorded")

    cache = stats.get("cache_hit_ratio")
    if cache is not None and cache < CACHE_HIT_WARN:
        add("warn", f"low cache hit ratio {cache:.1%}")

    return out


@register("postgres")
class PostgresConnector:
    def __init__(self, settings: Settings) -> None:
        self.dsn = settings.database_url

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.dsn:
            log.warning("postgres_skipped", reason="missing DATABASE_URL")
            return []
        try:
            import psycopg  # lazy: only needed when actually connecting
        except ImportError:
            log.error("postgres_skipped", reason='pip install "psycopg[binary]"')
            return []

        try:
            with psycopg.connect(self.dsn, connect_timeout=10) as conn:
                stats = self._collect(conn)
        except Exception as exc:  # noqa: BLE001 - a down DB shouldn't crash the run
            log.error("postgres_fetch_failed", error=str(exc))
            return [
                Signal(
                    source="postgres",
                    kind="db_health",
                    severity="error",
                    ts=datetime.now(UTC),
                    entity="database",
                    message=f"could not connect: {exc}",
                    raw={},
                )
            ]

        signals = _evaluate(stats)
        # Always leave a record that the check ran, even when healthy.
        signals.append(
            Signal(
                source="postgres",
                kind="db_health",
                severity="info",
                ts=datetime.now(UTC),
                entity="database",
                message=f"health check ok: {stats}",
                raw=stats,
            )
        )
        log.info("postgres_checked", problems=len(signals) - 1)
        return signals

    def _collect(self, conn) -> dict[str, float | int | None]:
        def scalar(sql: str):
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return row[0] if row else None

        return {
            "connections_used": scalar("SELECT count(*) FROM pg_stat_activity"),
            "connections_max": scalar("SELECT current_setting('max_connections')::int"),
            "long_queries": scalar(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state = 'active' AND now() - query_start > interval '30 seconds'"
            ),
            "deadlocks": scalar("SELECT coalesce(sum(deadlocks), 0) FROM pg_stat_database"),
            "cache_hit_ratio": scalar(
                "SELECT sum(blks_hit)::float / nullif(sum(blks_hit) + sum(blks_read), 0) "
                "FROM pg_stat_database"
            ),
        }
