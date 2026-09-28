"""OpenClaw CLI — the button.

    openclaw run --since 7d --sources loki,demo
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import typer

from openclaw.config import get_settings

app = typer.Typer(add_completion=False, help="On-demand DevSecOps analyst.")

_WINDOW_RE = re.compile(r"^(\d+)([mhd])$")
_UNIT = {"m": "minutes", "h": "hours", "d": "days"}


def parse_window(text: str) -> timedelta:
    m = _WINDOW_RE.match(text.strip())
    if not m:
        raise typer.BadParameter("use e.g. 30m, 24h, 7d")
    return timedelta(**{_UNIT[m.group(2)]: int(m.group(1))})


@app.command()
def run(
    since: str = typer.Option("1d", help="Time window: 30m, 24h, 7d, 30d..."),
    sources: str = typer.Option("demo", help="Comma-separated sources, e.g. loki,demo"),
    snapshot_only: bool = typer.Option(False, help="Snapshot only; skip analysis"),
    apply: bool = typer.Option(
        False, help="Let the agent stage fixes on a review branch (never pushed)."
    ),
) -> None:
    """Snapshot logs from the given sources to Parquet, then analyze them."""
    from openclaw.runner import execute

    settings = get_settings()
    result = execute(
        settings,
        parse_window(since),
        sources.split(","),
        snapshot_only=snapshot_only,
        apply=apply,
    )
    for name, n in result["counts"].items():
        typer.echo(f"  {name}: {n} signals")
    typer.echo(f"Snapshot written to {result['run_dir']} ({result['total']} signals)")

    if result.get("report"):
        typer.echo(f"\n{result['report']}")
    elif result.get("note"):
        typer.echo(f"\n{result['note']}")


@app.command()
def sources() -> None:
    """List the available connector sources."""
    from openclaw.connectors.base import REGISTRY

    for name in sorted(REGISTRY):
        typer.echo(name)


@app.command()
def archive(
    older_than: str = typer.Option("30d", help="Archive snapshots older than this (e.g. 30d)."),
    delete_local: bool = typer.Option(False, help="Delete local copies after upload."),
) -> None:
    """Upload old local Parquet snapshots to Cloudflare R2 (cold storage)."""
    from openclaw.archive import archive_old

    keys = archive_old(get_settings(), parse_window(older_than), delete_local=delete_local)
    typer.echo(f"Archived {len(keys)} object(s) to R2." if keys else "Nothing to archive.")


@app.command()
def stats(
    run_id: str = typer.Argument(..., help="Snapshot run id (see `openclaw run` output)."),
    freq: str = typer.Option("1h", help="Time bucket size for the series/anomalies."),
    clusters: int = typer.Option(5, help="Number of log-template clusters."),
) -> None:
    """Data-science analytics over a snapshot (no LLM): time-series, anomalies, clusters."""
    from openclaw.analytics import render_summary, summary

    run_dir = get_settings().snapshots_dir / run_id
    if not run_dir.is_dir():
        raise typer.BadParameter(f"no snapshot run: {run_id}")
    typer.echo(render_summary(summary(run_dir, freq=freq, k=clusters)))


@app.command("build-db")
def build_db(
    export_csv: bool = typer.Option(False, help="Also export CSV files for Power BI / Looker Studio."),
    export_dir: str = typer.Option("stages/04_warehouse/exports", help="Directory to write CSVs into."),
) -> None:
    """Build (or rebuild) the persistent DuckDB warehouse from all snapshots.

    Creates openclaw.duckdb with three mart tables:
      mart_signals      — full deduplicated fact table
      mart_hourly       — error counts + rates per source per hour
      mart_top_entities — top entities by error count

    Connect Power BI Desktop to openclaw.duckdb via:
      DuckDB ODBC driver: https://duckdb.org/docs/api/odbc/overview

    Or import the CSV exports directly into Power BI / Looker Studio.
    """
    from openclaw.warehouse import build

    settings = get_settings()
    db_path = settings.snapshots_dir.parent / "stages" / "04_warehouse" / "openclaw.duckdb"
    result = build(
        settings.snapshots_dir,
        db_path,
        export_dir=Path(export_dir) if export_csv else None,
    )
    typer.echo(f"Warehouse built: {result['db']}  ({result['total_signals']:,} signals)")
    if result.get("exports"):
        typer.echo(f"CSV exports: {result['exports']}/")
        typer.echo("  mart_signals.csv      — full fact table")
        typer.echo("  mart_hourly.csv       — hourly error rates (use for line charts)")
        typer.echo("  mart_top_entities.csv — top entities by error count (use for bar charts)")
        typer.echo("\nPower BI: Home → Get Data → Text/CSV → select each file")
        typer.echo("Looker Studio: + Add data → File Upload → select each file")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the dashboard (list runs, view reports, trigger runs)."""
    import uvicorn

    uvicorn.run("openclaw.api:app", host=host, port=port)


if __name__ == "__main__":
    app()
