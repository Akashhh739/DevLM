"""Persistent DuckDB warehouse — builds mart tables from all snapshots.

`openclaw build-db` reads every Parquet snapshot, creates a persistent
openclaw.duckdb file with three mart tables, and optionally exports CSVs
for Power BI / Looker Studio.

Mart tables (queryable from any tool via DuckDB ODBC or direct file):
  mart_signals       — deduplicated signal-level fact table
  mart_hourly        — pre-aggregated error counts + rates per source per hour
  mart_top_entities  — top entities by error count across all runs
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import structlog

log = structlog.get_logger()

DB_NAME = "openclaw.duckdb"


def build(snapshots_dir: Path, db_path: Path, export_dir: Path | None = None) -> dict:
    snapshots_dir = snapshots_dir.resolve()
    parquet_glob = str(snapshots_dir / "**" / "*.parquet")

    con = duckdb.connect(str(db_path))

    # ── raw signals ──────────────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS mart_signals")
    con.execute(f"""
        CREATE TABLE mart_signals AS
        SELECT DISTINCT
            source,
            kind,
            severity,
            CASE severity
                WHEN 'critical' THEN 4
                WHEN 'error'    THEN 3
                WHEN 'warn'     THEN 2
                ELSE 1
            END AS severity_rank,
            ts::TIMESTAMPTZ                         AS ts,
            ts::DATE                                AS event_date,
            date_trunc('hour', ts::TIMESTAMPTZ)     AS event_hour,
            entity,
            message,
        FROM read_parquet('{parquet_glob}', hive_partitioning=false)
        WHERE source IS NOT NULL
    """)
    total = con.execute("SELECT COUNT(*) FROM mart_signals").fetchone()[0]

    # ── hourly mart ──────────────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS mart_hourly")
    con.execute("""
        CREATE TABLE mart_hourly AS
        SELECT
            event_date,
            event_hour,
            source,
            COUNT(*)                                                AS total_signals,
            SUM(CASE WHEN severity_rank >= 3 THEN 1 ELSE 0 END)    AS errors,
            SUM(CASE WHEN severity_rank = 4  THEN 1 ELSE 0 END)    AS criticals,
            COUNT(DISTINCT entity)                                  AS unique_entities,
            ROUND(
                SUM(CASE WHEN severity_rank >= 3 THEN 1 ELSE 0 END)
                * 100.0 / COUNT(*), 2
            )                                                       AS error_rate_pct
        FROM mart_signals
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """)

    # ── top entities mart ────────────────────────────────────────────────────
    con.execute("DROP TABLE IF EXISTS mart_top_entities")
    con.execute("""
        CREATE TABLE mart_top_entities AS
        SELECT
            source,
            entity,
            COUNT(*)                                                AS total_signals,
            SUM(CASE WHEN severity_rank >= 3 THEN 1 ELSE 0 END)    AS errors,
            SUM(CASE WHEN severity_rank = 4  THEN 1 ELSE 0 END)    AS criticals,
            MIN(ts)                                                 AS first_seen,
            MAX(ts)                                                 AS last_seen,
        FROM mart_signals
        WHERE severity_rank >= 2
        GROUP BY 1, 2
        ORDER BY errors DESC
    """)

    con.execute("CHECKPOINT")
    log.info("warehouse_built", db=str(db_path), rows=total)

    result = {"db": str(db_path), "total_signals": total}

    # ── optional CSV export for Power BI / Looker Studio ─────────────────────
    if export_dir:
        export_dir.mkdir(parents=True, exist_ok=True)
        for table in ("mart_signals", "mart_hourly", "mart_top_entities"):
            out = export_dir / f"{table}.csv"
            con.execute(f"COPY {table} TO '{out}' (HEADER, DELIMITER ',')")
            log.info("csv_exported", table=table, path=str(out))
        result["exports"] = str(export_dir)

    con.close()
    return result


def query(db_path: Path, sql: str) -> list[dict]:
    con = duckdb.connect(str(db_path), read_only=True)
    rel = con.execute(sql)
    cols = [d[0] for d in rel.description]
    rows = [dict(zip(cols, r)) for r in rel.fetchall()]
    con.close()
    return rows
