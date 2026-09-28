"""Cold archive: push old local Parquet snapshots to Cloudflare R2.

Snapshots are already Parquet (zstd), so archiving is just an upload. Old run dirs
are shipped to R2 under a Hive-style prefix, then optionally deleted locally.
Query the archive later with DuckDB over `s3://`. R2 is S3-compatible (boto3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from openclaw.config import Settings

log = structlog.get_logger()

_RUN_FMT = "%Y%m%dT%H%M%SZ"


def _run_age(run_id: str, now: datetime) -> timedelta:
    """Age of a run dir named like 20260927T140644Z."""
    started = datetime.strptime(run_id, _RUN_FMT).replace(tzinfo=UTC)
    return now - started


def select_old_runs(run_ids: list[str], older_than: timedelta, now: datetime) -> list[str]:
    """Which run ids are older than the cutoff (unparseable names are skipped)."""
    out = []
    for rid in run_ids:
        try:
            if _run_age(rid, now) > older_than:
                out.append(rid)
        except ValueError:
            continue
    return sorted(out)


def object_key(run_id: str, filename: str) -> str:
    """Hive-partitioned key: raw_signals/run=<id>/<file>."""
    return f"raw_signals/run={run_id}/{filename}"


def _s3_client(settings: Settings) -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url(),
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def archive_old(settings: Settings, older_than: timedelta, delete_local: bool = False) -> list[str]:
    """Upload run dirs older than `older_than` to R2. Returns the keys uploaded."""
    if not (settings.r2_bucket and settings.r2_access_key_id):
        log.warning("archive_skipped", reason="R2 not configured")
        return []

    base = settings.snapshots_dir
    if not base.is_dir():
        return []
    run_ids = [d.name for d in base.iterdir() if d.is_dir()]
    old = select_old_runs(run_ids, older_than, datetime.now(UTC))

    client = _s3_client(settings)
    uploaded: list[str] = []
    for rid in old:
        run_dir = base / rid
        for pq in sorted(run_dir.glob("*.parquet")):
            key = object_key(rid, pq.name)
            client.upload_file(str(pq), settings.r2_bucket, key)
            uploaded.append(key)
        if delete_local:
            for f in run_dir.iterdir():
                f.unlink()
            run_dir.rmdir()
    log.info("archived", runs=len(old), objects=len(uploaded), deleted_local=delete_local)
    return uploaded


def query_archive(settings: Settings, sql: str) -> list[dict[str, Any]]:
    """Run read-only SQL over the R2-archived Parquet via DuckDB (view `signals`)."""
    import duckdb

    con = duckdb.connect(database=":memory:")
    try:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(f"SET s3_endpoint='{settings.r2_account_id}.r2.cloudflarestorage.com'")
        con.execute(f"SET s3_access_key_id='{settings.r2_access_key_id}'")
        con.execute(f"SET s3_secret_access_key='{settings.r2_secret_access_key}'")
        con.execute("SET s3_url_style='path'; SET s3_region='auto'")
        glob = f"s3://{settings.r2_bucket}/raw_signals/**/*.parquet"
        con.execute(f"CREATE VIEW signals AS SELECT * FROM read_parquet('{glob}')")
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r)) for r in rel.fetchmany(500)]
    finally:
        con.close()
