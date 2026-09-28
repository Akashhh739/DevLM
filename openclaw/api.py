"""Minimal dashboard: list past runs, view reports, trigger a new run.

Self-contained FastAPI app (one served HTML page, no build step). Runs the same
pipeline as the CLI via `runner.execute`. Start with `openclaw serve`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from openclaw.cli import parse_window
from openclaw.config import get_settings
from openclaw.runner import execute

app = FastAPI(title="OpenClaw")


def _valid_run_id(run_id: str) -> bool:
    return "/" not in run_id and ".." not in run_id


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    base = get_settings().snapshots_dir
    runs: list[dict[str, Any]] = []
    if base.is_dir():
        for d in sorted(base.iterdir(), reverse=True):
            if d.is_dir():
                runs.append(
                    {
                        "run_id": d.name,
                        "sources": sorted(p.stem for p in d.glob("*.parquet")),
                        "has_report": (d / "report.md").exists(),
                    }
                )
    return runs


@app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
def get_report(run_id: str) -> str:
    if not _valid_run_id(run_id):
        raise HTTPException(status_code=400, detail="bad run id")
    report = get_settings().snapshots_dir / run_id / "report.md"
    if not report.exists():
        raise HTTPException(status_code=404, detail="no report for this run")
    return report.read_text()


class RunRequest(BaseModel):
    since: str = "1d"
    sources: str = "demo"
    snapshot_only: bool = False
    apply: bool = False


@app.post("/api/run")
def trigger_run(req: RunRequest) -> dict[str, Any]:
    return execute(
        get_settings(),
        parse_window(req.since),
        req.sources.split(","),
        snapshot_only=req.snapshot_only,
        apply=req.apply,
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _HTML


_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>OpenClaw</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
 header{padding:16px 24px;border-bottom:1px solid #1e293b;font-size:20px;font-weight:600}
 header span{color:#3b82f6}
 main{display:flex;gap:20px;padding:20px}
 .panel{background:#1e293b;border-radius:12px;padding:16px;flex:1}
 input,button{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0}
 button{background:#3b82f6;border:none;cursor:pointer;font-weight:600}
 .run{padding:8px;border-bottom:1px solid #334155;cursor:pointer}
 .run:hover{background:#243044}
 pre{white-space:pre-wrap;background:#0f172a;padding:12px;border-radius:8px;overflow:auto;max-height:70vh}
 .muted{color:#94a3b8;font-size:13px}
</style></head><body>
<header>Open<span>Claw</span> — analyst</header>
<main>
 <div class="panel" style="max-width:360px">
  <h3>New run</h3>
  <div class="muted">window &amp; sources</div>
  <p><input id="since" value="1d" size="6"> <input id="sources" value="demo" size="18"></p>
  <p><label><input type="checkbox" id="snap"> snapshot only</label></p>
  <button onclick="trigger()">Run</button>
  <h3 style="margin-top:24px">Runs</h3>
  <div id="runs" class="muted">loading…</div>
 </div>
 <div class="panel">
  <h3 id="rtitle">Report</h3>
  <pre id="report" class="muted">Select a run with a report.</pre>
 </div>
</main>
<script>
async function loadRuns(){
  const r = await fetch('/api/runs'); const runs = await r.json();
  document.getElementById('runs').innerHTML = runs.map(x =>
    `<div class="run" onclick="viewReport('${x.run_id}')">${x.run_id}<br>
     <span class="muted">${x.sources.join(', ')||'—'} ${x.has_report?'· report':''}</span></div>`
  ).join('') || '<div class="muted">no runs yet</div>';
}
async function viewReport(id){
  document.getElementById('rtitle').textContent = 'Report — '+id;
  const r = await fetch(`/api/runs/${id}/report`);
  document.getElementById('report').textContent = r.ok ? await r.text() : 'No report for this run.';
}
async function trigger(){
  const body = {since:document.getElementById('since').value,
                sources:document.getElementById('sources').value,
                snapshot_only:document.getElementById('snap').checked};
  document.getElementById('runs').innerHTML='running…';
  const r = await fetch('/api/run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  const res = await r.json(); await loadRuns();
  if(res.report){ viewReport(res.run_id); }
}
loadRuns();
</script></body></html>"""
