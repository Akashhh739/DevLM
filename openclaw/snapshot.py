"""Write normalized signals to a Parquet snapshot on local disk.

The Parquet file(s) under `snapshots/<run-id>/` are the only "store" — no database.
The agent later queries them with DuckDB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from openclaw.models import Signal

SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("kind", pa.string()),
        ("severity", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("entity", pa.string()),
        ("message", pa.string()),
        ("raw_json", pa.string()),
    ]
)


def new_run_dir(base: Path) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_snapshot(signals: list[Signal], run_dir: Path, source: str) -> Path:
    """Write one source's signals to `<run_dir>/<source>.parquet` (zstd)."""
    table = pa.Table.from_pylist([s.to_row() for s in signals], schema=SCHEMA)
    path = run_dir / f"{source}.parquet"
    pq.write_table(table, path, compression="zstd")
    return path
