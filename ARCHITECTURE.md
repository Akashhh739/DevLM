# OpenClaw — Deep Technical Architecture

> On-demand DevSecOps analyst. One command pulls logs from any source, writes them to Parquet, and runs a GPT agent over the data + your local codebase to produce a grounded diagnosis with proposed code fixes.

---

## Table of Contents

1. [Quick Start — New Person Setup](#1-quick-start--new-person-setup)
2. [Project Layout](#2-project-layout)
3. [Full Architecture](#3-full-architecture)
4. [The Signal — Core Data Model](#4-the-signal--core-data-model)
5. [How Parquet Files Are Made](#5-how-parquet-files-are-made)
6. [Connectors — How Data Is Collected](#6-connectors--how-data-is-collected)
7. [The Jev Pre-Ranker (System-1 Triage)](#7-the-jev-pre-ranker-system-1-triage)
8. [The OpenAI Agent Loop](#8-the-openai-agent-loop)
9. [Agent Tools — Every Method Explained](#9-agent-tools--every-method-explained)
10. [The Warehouse (DuckDB)](#10-the-warehouse-duckdb)
11. [Every File Explained](#11-every-file-explained)
12. [Data Flow — End to End](#12-data-flow--end-to-end)
13. [PR & Interview Questions](#13-pr--interview-questions)

---

## 1. Quick Start — New Person Setup

### Prerequisites

- Python 3.11+
- Git
- Your local codebase checked out somewhere (e.g. `~/code/my-backend`)
- API keys for whichever sources you want to collect from

### Step 1 — Clone and install

```bash
git clone https://github.com/your-org/openclaw
cd openclaw
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# core only (demo + DNS connectors work immediately)
pip install -e .

# add AWS connectors
pip install -e ".[aws]"

# add Postgres/Supabase connectors
pip install -e ".[postgres]"

# everything
pip install -e ".[aws,postgres]"
```

### Step 2 — Create your `.env`

Copy the example and fill in what you have. **Every connector is optional** — if its key is missing it is silently skipped.

```bash
cp .env.example .env
```

Minimum to get started (no real credentials needed):

```env
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
REPO_PATH=/path/to/your/backend/repo
```

Fill in more as you go:

```env
# AWS App Runner logs
AWS_REGION=ap-south-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# Supabase auth audit
SUPABASE_DB_URL=postgresql://postgres.xxx:password@aws-0-ap-south-1.pooler.supabase.com:5432/postgres

# Same DB for Postgres health stats
DATABASE_URL=postgresql://...

# Cloudflare WAF + Workers
CLOUDFLARE_API_TOKEN=cfat_...
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_ZONE_ID=...

# Sentry errors
SENTRY_TOKEN=sntryu_...
SENTRY_ORG=your-org
SENTRY_PROJECT=python,javascript-nextjs

# GitHub CI failures
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=your-org/your-repo

# DNS hosts to monitor (no credentials needed)
DNS_HOSTS=api.example.com,app.example.com

# Optional: cheaper fast pre-ranker via OpenRouter
JEV_API_KEY=sk-or-v1-...
JEV_BASE_URL=https://openrouter.ai/api/v1
JEV_MODEL=openai/gpt-4o-mini
```

### Step 3 — Smoke test with demo data

```bash
# Uses the built-in synthetic demo connector — no real credentials
openclaw run --sources demo --since 1d
```

Expected output:
```
  demo: 5 signals
Snapshot written to snapshots/20260928T091335Z (5 signals)

## Summary
Found 2 errors in the billing service...
```

### Step 4 — Run against real sources

```bash
# pull 7 days from all configured connectors
openclaw run --since 7d --sources aws,cloudwatch_logs,sentry,postgres,dns,cloudflare,cf_workers

# snapshot only (no GPT call — useful for checking connector output first)
openclaw run --since 7d --sources sentry --snapshot-only

# include a proposed fix on a review branch
openclaw run --since 1d --sources cloudwatch_logs,sentry --apply
```

### Step 5 — Add your runbooks

Create `rules/job-queue.md` (copy `rules/job-queue.example.md`) and fill in your specific services. The agent reads these at analysis time so it knows what's intentional.

### Step 6 — Build the warehouse

```bash
# builds stages/04_warehouse/openclaw.duckdb from all snapshots
openclaw build-db

# also export CSVs for Power BI / Looker Studio
openclaw build-db --export-csv
```

### Step 7 — Query it directly

```bash
python3 -c "
import duckdb
con = duckdb.connect('stages/04_warehouse/openclaw.duckdb', read_only=True)
print(con.execute('SELECT source, errors, error_rate_pct FROM mart_hourly ORDER BY errors DESC LIMIT 10').df())
"
```

### Available CLI commands

```
openclaw run          Pull + analyze (the main command)
openclaw sources      List all available connector names
openclaw stats        Data-science analytics over a run (no LLM)
openclaw build-db     Build / rebuild the DuckDB warehouse
openclaw archive      Upload old snapshots to R2 cold storage
openclaw serve        Launch dashboard at localhost:8000
```

---

## 2. Project Layout

```
OpenClaw/
├── .env                        your credentials (git-ignored)
├── .env.example                template for the above
├── pyproject.toml              package + deps + tool config
│
├── openclaw/                   the Python package (all source code)
│   ├── cli.py                  CLI entry point (Typer)
│   ├── config.py               Settings (pydantic-settings, reads .env)
│   ├── models.py               Signal dataclass — the universal row type
│   ├── runner.py               orchestrates collect → snapshot → analyze
│   ├── snapshot.py             writes Parquet files (pyarrow, zstd)
│   ├── analyze.py              GPT tool-loop setup + system prompt
│   ├── llm.py                  raw OpenAI Responses API loop
│   ├── tools.py                agent tools: DuckDB, git, files, memory
│   ├── jev.py                  System-1 pre-ranker (cheap fast model)
│   ├── report.py               renders + saves the Markdown report
│   ├── apply.py                stages proposed fixes as a git branch
│   ├── warehouse.py            builds mart tables in openclaw.duckdb
│   ├── analytics.py            stats over a snapshot run (no LLM)
│   ├── archive.py              cold-archive to Cloudflare R2
│   ├── api.py                  FastAPI dashboard
│   └── connectors/
│       ├── base.py             Connector ABC + REGISTRY dict
│       ├── _util.py            shared HTTP helpers
│       ├── apprunner.py        AWS App Runner health + deploys
│       ├── aws.py              GuardDuty + CloudWatch metrics
│       ├── cloudwatch_logs.py  raw App Runner application logs
│       ├── cloudflare.py       WAF / firewall events
│       ├── cf_workers.py       Cloudflare Workers error rates
│       ├── cf_pages.py         Cloudflare Pages deploy failures
│       ├── sentry.py           error events + issue counts
│       ├── supabase.py         auth audit log
│       ├── postgres.py         pg_stat_* health views
│       ├── dns.py              DNS resolution checks
│       ├── github.py           failed Actions, open PRs
│       ├── loki.py             Grafana Loki log queries
│       ├── prometheus.py       Prometheus alerts
│       ├── ga4.py              Google Analytics 4 events
│       ├── k8s.py              Kubernetes pod events
│       └── demo.py             synthetic test fixtures
│
├── snapshots/                  one folder per run (git-ignored)
│   └── 20260928T091335Z/
│       ├── sentry.parquet
│       ├── cloudwatch_logs.parquet
│       └── report.md
│
├── rules/                      your runbooks (git-ignored — private)
│   ├── job-queue.example.md    generic template to copy + fill in
│   └── example-oom.md          example OOMKilled runbook
│
├── memory/                     agent-learned facts across runs (git-ignored)
│
├── config/
│   └── constraints.example.yaml  infra limits (max replicas, budgets)
│
├── docs/
│   ├── connectors.md           connector setup + required token scopes
│   └── scheduling.md           how to automate with GitHub Actions / cron
│
└── stages/                     learning path + warehouse (git-ignored)
    └── 04_warehouse/
        ├── openclaw.duckdb     the live warehouse
        ├── exports/            Power BI CSVs
        ├── lake/               Spark silver + gold Parquet
        └── 01_dbt_local/ … 04_databricks/
```

---

## 3. Full Architecture

```
  $ openclaw run --since 7d --sources sentry,cloudwatch_logs,postgres
         │
         ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  1. COLLECT                                                     │
  │                                                                 │
  │  connector.fetch(window: timedelta) → list[Signal]              │
  │                                                                 │
  │  sentry.py ──────────► Signal(source="sentry", kind="error")   │
  │  cloudwatch_logs.py ─► Signal(source="cloudwatch_logs", ...)   │
  │  postgres.py ────────► Signal(source="postgres", kind="metric") │
  │  (each is its own file, same interface, runs sequentially)      │
  └───────────────────────────────┬─────────────────────────────────┘
                                  │ list[Signal] per source
                                  ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  2. SNAPSHOT (snapshot.py)                                      │
  │                                                                 │
  │  Signal.to_row() → dict  →  pa.Table (pyarrow)                 │
  │                          →  Parquet (zstd)                      │
  │                                                                 │
  │  snapshots/20260928T091335Z/                                    │
  │    sentry.parquet            (7 columns, typed schema)          │
  │    cloudwatch_logs.parquet                                      │
  │    postgres.parquet                                             │
  └───────────────────────────────┬─────────────────────────────────┘
                                  │ run_dir path
                                  ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  3. JEV PRE-RANK (jev.py)   [optional — System-1 triage]       │
  │                                                                 │
  │  DuckDB GROUP BY (source, entity, severity)  → 50 clusters     │
  │  cheap fast model (gpt-4o-mini via OpenRouter)                  │
  │    → ranks clusters by urgency                                  │
  │    → returns top 8 indices                                      │
  │                                                                 │
  │  Purpose: skip the expensive reasoning model past noise         │
  │  Fallback: if Jev fails/missing → top 8 by volume              │
  └───────────────────────────────┬─────────────────────────────────┘
                                  │ focus clusters (text)
                                  ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  4. ANALYZE (analyze.py + llm.py)                               │
  │                                                                 │
  │  System prompt contains:                                        │
  │    - Role + rules (what is a bug vs intentional)                │
  │    - Repo file tree (real paths, top 80 source files)           │
  │    - Rules from rules/*.md (your runbooks)                      │
  │    - Memory from memory/*.md (past learnings)                   │
  │    - Focus clusters from Jev                                    │
  │                                                                 │
  │  OpenAI Responses API tool-use loop (max 32 iterations):        │
  │                                                                 │
  │   GPT calls tool ──► tool executes ──► result fed back ──┐      │
  │        ▲                                                 │      │
  │        └─────────────────────────────────────────────────┘      │
  │                                                                 │
  │  Tools available:                                               │
  │    duckdb_query   → SQL over the Parquet snapshot               │
  │    read_file      → read source files from your repo            │
  │    grep           → git grep across the codebase                │
  │    list_dir       → list real paths (avoid guessing)            │
  │    git_log        → recent commits (optional: filter by path)   │
  │    git_blame      → who/when/why changed a specific line        │
  │    git_diff       → what changed between two refs               │
  │    append_memory  → write a durable learning for next run       │
  │    write_fix      → (only with --apply) stage a fix branch      │
  └───────────────────────────────┬─────────────────────────────────┘
                                  │ final text (Markdown)
                                  ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  5. REPORT (report.py)                                          │
  │                                                                 │
  │  Saved to snapshots/<run_id>/report.md                          │
  │                                                                 │
  │  ## Summary                                                     │
  │  ## Findings  (evidence + bug-vs-intentional + proposed fix)    │
  │  ## Recommendations                                             │
  └─────────────────────────────────────────────────────────────────┘
```

---

## 4. The Signal — Core Data Model

Every connector, regardless of source, maps its raw payload to a single `Signal` dataclass defined in `openclaw/models.py`:

```python
@dataclass(slots=True)
class Signal:
    source:   str       # "sentry" | "cloudwatch_logs" | "postgres" | ...
    kind:     str       # "error" | "log" | "metric" | "waf_event" | "dns_check" | ...
    severity: str       # "critical" | "error" | "warn" | "info"
    ts:       datetime  # event timestamp, UTC
    entity:   str       # pod / service / zone / function the signal is about
    message:  str       # human-readable one-line summary
    raw:      dict      # original API response payload (full fidelity)
```

**Why a uniform schema?**
The agent runs one SQL query (`SELECT * FROM signals`) that spans all sources simultaneously. Without normalization, querying Sentry errors alongside CloudWatch logs would require source-specific joins. The `Signal` abstraction means `WHERE severity = 'error' AND source IN ('sentry', 'cloudwatch_logs')` works identically across every connector.

**`to_row()`** flattens the Signal to a dict for Parquet:
```python
def to_row(self) -> dict:
    return {
        "source":   self.source,
        "kind":     self.kind,
        "severity": self.severity,
        "ts":       self.ts,           # datetime → pa.timestamp("us", tz="UTC")
        "entity":   self.entity,
        "message":  self.message,
        "raw_json": json.dumps(self.raw, default=str),  # arbitrary dict → JSON string
    }
```

The `raw` dict is serialized to a JSON string (`raw_json`) in Parquet so the typed schema stays fixed regardless of what the raw payload contains. The agent can `json_extract` it if it needs the original fields.

---

## 5. How Parquet Files Are Made

Source: `openclaw/snapshot.py`

### The pyarrow schema

```python
SCHEMA = pa.schema([
    ("source",   pa.string()),
    ("kind",     pa.string()),
    ("severity", pa.string()),
    ("ts",       pa.timestamp("us", tz="UTC")),   # microsecond precision, UTC
    ("entity",   pa.string()),
    ("message",  pa.string()),
    ("raw_json", pa.string()),
])
```

The schema is declared upfront and enforced on every write. This ensures all Parquet files across all connectors have identical column types — required for DuckDB to union them with `read_parquet('**/*.parquet')`.

### The write path

```python
def write_snapshot(signals: list[Signal], run_dir: Path, source: str) -> Path:
    table = pa.Table.from_pylist([s.to_row() for s in signals], schema=SCHEMA)
    path  = run_dir / f"{source}.parquet"
    pq.write_table(table, path, compression="zstd")
    return path
```

Step by step:
1. Each Signal is converted to a plain `dict` via `to_row()`
2. `pa.Table.from_pylist(rows, schema=SCHEMA)` builds a columnar Arrow table in memory — Arrow stores each column contiguously (not row by row), which is what makes Parquet column-pruning fast
3. `pq.write_table(..., compression="zstd")` serializes the Arrow table to disk as Parquet with ZSTD compression — typically 5–10× smaller than raw JSON

### Why Parquet and not SQLite / JSON / CSV?

| Format | Size | Schema-enforced | Column-pruning | DuckDB-native |
|---|---|---|---|---|
| JSON lines | large | no | no | slow scan |
| CSV | medium | no | no | slow scan |
| SQLite | medium | yes | no | via extension |
| **Parquet (zstd)** | **small** | **yes** | **yes** | **native** |

DuckDB reads Parquet natively: `SELECT source, count(*) FROM read_parquet('**/*.parquet') GROUP BY 1` scans only the `source` column — it never touches `raw_json`. This is called **column pruning** and is the reason a 10,000-row snapshot analyzes in milliseconds.

### Run directory naming

```python
run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")  # e.g. 20260928T091335Z
run_dir = base / run_id
```

ISO 8601 format sorts lexicographically — `ls snapshots/` gives runs in chronological order automatically.

### How DuckDB reads across multiple runs

```python
# In tools.py — the agent sees ALL runs merged into one view
glob = str(self.run_dir / "*.parquet")
con.execute(f"CREATE VIEW signals AS SELECT * FROM read_parquet('{glob}')")
```

For the warehouse (`warehouse.py`), it reads across ALL runs ever:
```python
parquet_glob = str(snapshots_dir / "**" / "*.parquet")
con.execute(f"CREATE TABLE mart_signals AS SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=false)")
```

The `**` glob traverses into subdirectories — every `snapshots/<run_id>/<source>.parquet` file gets unioned.

---

## 6. Connectors — How Data Is Collected

Every connector inherits from `Connector` (defined in `connectors/base.py`) and implements one method:

```python
class Connector(ABC):
    name: str                                 # must match the CLI --sources flag
    
    @abstractmethod
    def fetch(self, window: timedelta) -> list[Signal]:
        """Pull data for the given time window. Return [] if unconfigured."""
        ...
```

The `REGISTRY` dict maps name strings to connector classes. `build_connectors(sources, settings)` looks up each name, instantiates it with the settings, and returns the list.

### Connector inventory

| Name | Source | Data type | Credentials needed |
|---|---|---|---|
| `apprunner` | AWS App Runner | service health, recent deploys | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| `aws` | GuardDuty + CloudWatch | threat findings, CPU/memory metrics | same AWS creds |
| `cloudwatch_logs` | CloudWatch Logs | raw application log lines | same AWS creds |
| `cloudflare` | Cloudflare WAF | firewall events, blocked requests | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ZONE_ID` |
| `cf_workers` | Cloudflare Workers | error rates, CPU time | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` |
| `cf_pages` | Cloudflare Pages | failed deployments | `CLOUDFLARE_API_TOKEN` (needs Pages:Read scope) |
| `sentry` | Sentry | error events, issue counts | `SENTRY_TOKEN` + `SENTRY_ORG` + `SENTRY_PROJECT` |
| `supabase` | Supabase Postgres | auth audit log | `SUPABASE_DB_URL` |
| `postgres` | Any Postgres | `pg_stat_*` health views | `DATABASE_URL` |
| `dns` | stdlib resolver | DNS resolution success/failure | none — `DNS_HOSTS` only |
| `github` | GitHub API | failed Actions, open PRs | `GITHUB_TOKEN` + `GITHUB_REPO` |
| `loki` | Grafana Loki | log query results | `LOKI_URL` |
| `prometheus` | Prometheus | active alerts | `PROMETHEUS_URL` |
| `ga4` | Google Analytics 4 | user events | `GA4_PROPERTY_ID` + service account JSON |
| `k8s` | Kubernetes | pod events, OOMKilled | kubeconfig / in-cluster |
| `demo` | built-in | synthetic fixtures | none |

### How `cloudwatch_logs` works (example deep-dive)

```python
# 1. Auto-discover App Runner log groups
logs_client.describe_log_groups(logGroupNamePrefix="/aws/apprunner/")
# Returns: /aws/apprunner/my-service/abc123/application
#          /aws/apprunner/my-service/abc123/service

# 2. Filter log events for error patterns
filter_pattern = "?error ?exception ?traceback ?critical ?fatal ?warn ?failed ?timeout ?refused ?5xx"
logs_client.filter_log_events(
    logGroupName=group,
    startTime=int((now - window).timestamp() * 1000),  # milliseconds epoch
    filterPattern=filter_pattern,
    limit=10_000
)

# 3. Normalize each matching line → Signal
Signal(
    source="cloudwatch_logs",
    kind="log",
    severity=_classify(line),  # "error" if "error"/"exception"/"traceback" in line
    ts=datetime.fromtimestamp(event["timestamp"] / 1000, UTC),
    entity=service_name,        # extracted from log group path
    message=event["message"][:500],
    raw={"log_group": group, "log_stream": event["logStreamName"], "event_id": event["eventId"]}
)
```

### How `sentry` handles multiple projects

```python
# SENTRY_PROJECT=python,javascript-nextjs → two separate fetches
for project_slug in settings.sentry_project.split(","):
    signals += _fetch_project(project_slug, ...)
```

This means one `sentry.parquet` file contains errors from all your Sentry projects merged.

### Connector skip logic

Every connector checks at the start of `fetch()`:
```python
def fetch(self, window):
    if not self.settings.sentry_token:
        log.info("sentry_skipped")
        return []
```

This means `openclaw run --sources sentry,postgres,demo` works even if only `demo` credentials are configured — the others produce empty Parquet files (or no files at all) and the agent sees fewer signals.

---

## 7. The Jev Pre-Ranker (System-1 Triage)

Source: `openclaw/jev.py`

The expensive reasoning model (GPT-4o) costs ~$0.03 per 1K tokens. A raw snapshot with 8,000 signals would burn the entire context window on noise. Jev solves this.

### Step 1 — Cluster the snapshot

```python
_CLUSTER_SQL = """
    SELECT source, entity, severity, count(*) AS n, min(message) AS sample
    FROM signals
    GROUP BY 1, 2, 3
    ORDER BY n DESC
    LIMIT 50
"""
clusters = tools.duckdb_query(_CLUSTER_SQL)
```

This collapses 8,000 raw signals into at most 50 `(source, entity, severity)` groups — each with a count and one sample message. A snapshot with 333 Sentry errors across 3 services becomes 3 clusters, not 333 rows.

### Step 2 — Rank by urgency (cheap model call)

```python
listing = "\n".join(
    f"{i}: {c['severity']} {c['source']}/{c['entity']} n={c['n']} :: {c['sample']}"
    for i, c in enumerate(clusters)
)

resp = client.chat.completions.create(
    model="openai/gpt-4o-mini",           # via OpenRouter — ~$0.0001 per call
    messages=[
        {"role": "system",  "content": "Respond ONLY with a JSON array of integers."},
        {"role": "user",    "content": f"Rank by urgency for a DevSecOps engineer:\n\n{listing}"}
    ]
)
# Returns: [3, 7, 1, 0, 5, ...]  — indices, most important first
```

The response is a JSON array of integers (cluster indices). This is then used to reorder the cluster list:
```python
ranked = [clusters[i] for i in order if 0 <= i < len(clusters)]
return ranked[:top]   # top 8 by default
```

### Step 3 — Focus the expensive model

The top 8 clusters (not all 50, not all 8,000 rows) are injected into the user message:
```
Focus areas (pre-ranked):
- error sentry/billing-service (n=333): NoneType has no attribute 'charge'
- error cloudwatch_logs/api-server (n=47): Job moved to DEAD_LETTER
- warn cloudwatch_logs/api-server (n=7407): Storage sync download miss
```

The expensive model sees the errors ranked above the high-volume warnings — and starts investigating the right thing.

### Fallback

Any exception in Jev (network error, bad JSON, missing key) silently falls back to volume ordering:
```python
except Exception:
    log.warning("jev_prerank_failed", ...)
return clusters[:top]   # top 8 by volume — Jev failure never breaks a run
```

---

## 8. The OpenAI Agent Loop

Source: `openclaw/llm.py` + `openclaw/analyze.py`

### The Responses API vs Chat Completions API

OpenClaw uses the **Responses API** (`client.responses.create`), not the Chat Completions API (`client.chat.completions.create`). The difference matters:

| | Chat Completions | Responses API |
|---|---|---|
| Multi-turn | you manage history | `previous_response_id` threads server-side |
| Tool calling | `tools` param | `tools` param (same schema) |
| Context | you resend full history | server keeps it, you send only new items |
| Token cost | grows with turns | constant per turn |
| Reasoning models (o1, o4) | limited | full support |

The `previous_response_id` threading is why the loop is cheap even at 32 iterations — you only send the new `function_call_output` items each turn, not the entire conversation history.

### The loop in detail

```python
def run_tool_loop(client, model, instructions, user_input, tools, dispatch, max_iters=32):
    input_items = [{"role": "user", "content": user_input}]
    prev_id = None

    for i in range(max_iters):
        # --- call the model ---
        resp = client.responses.create(
            model=model,
            instructions=instructions,   # system prompt
            input=input_items,
            tools=tools,
            previous_response_id=prev_id  # threads prior turns server-side
        )
        prev_id = resp.id

        # --- check if done ---
        calls = [o for o in resp.output if o.type == "function_call"]
        if not calls:
            return resp.output_text    # model finished — no more tool calls

        # --- execute tools + feed results back ---
        input_items = []               # only new outputs, not full history
        for call in calls:
            args   = json.loads(call.arguments)
            result = dispatch(call.name, args)   # runs the actual tool
            input_items.append({
                "type":    "function_call_output",
                "call_id": call.call_id,
                "output":  str(result)[:20_000]   # cap at 20K chars per result
            })
```

### Tool schema format (OpenAI function-tool spec)

```python
{
    "type": "function",
    "name": "duckdb_query",
    "description": "Run read-only SQL over the log snapshot (view `signals`: source, kind, severity, ts, entity, message, raw_json).",
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {"type": "string"}
        },
        "required": ["sql"]
    }
}
```

The model sees all 8 (or 9 with `write_fix`) schemas in its context. It decides which to call based on the description. Good descriptions are load-bearing — a vague description causes the model to pick the wrong tool.

### What goes in the system prompt

```
You are OpenClaw, an on-demand DevSecOps analyst.
...grounding rules...
...apply_note (read-only vs --apply mode)...

--- REPO FILE TREE ---
app/main.py
app/services/job_queue.py
app/routers/worker.py
...                        ← actual paths from repo_file_tree()

--- RULES / RUNBOOKS ---
### job-queue.md
...your runbook content...

--- MEMORY ---
### billing-service-nullcheck.md
NoneType error in billing traced to missing null check in charge.py:42
```

Injecting the real file tree prevents the single most common agent failure: hallucinating a path like `src/services/queue.py` when the real path is `app/services/job_queue.py`. The agent never needs to guess — it reads the tree and uses `list_dir` if it needs to go deeper.

### A typical 10-iteration investigation

```
iter 1  duckdb_query:  SELECT severity, count(*) FROM signals GROUP BY 1
iter 2  duckdb_query:  SELECT entity, message, count(*) FROM signals WHERE severity='error' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20
iter 3  list_dir:      app/services/
iter 4  read_file:     app/services/job_queue.py
iter 5  grep:          _TERMINAL_ERROR_MARKERS
iter 6  git_blame:     app/services/job_queue.py  start=1450 end=1530
iter 7  git_log:       app/services/job_queue.py  limit=10
iter 8  duckdb_query:  SELECT message, count(*) FROM signals WHERE message LIKE '%DEAD_LETTER%' GROUP BY 1
iter 9  append_memory: {name: "dead-letter-is-intentional", content: "..."}
iter 10 (final text)   ## Summary ... ## Findings ... ## Recommendations
```

---

## 9. Agent Tools — Every Method Explained

Source: `openclaw/tools.py`

All tools are methods on the `Tools` class. They are **deterministic** — no randomness, fully unit-testable without a model.

### `duckdb_query(sql: str) → list[dict]`

```python
def duckdb_query(self, sql: str) -> list[dict]:
    if not _is_readonly_sql(sql):
        raise ToolError("only SELECT/WITH queries allowed")
    con = duckdb.connect(database=":memory:")       # fresh in-memory connection
    glob = str(self.run_dir / "*.parquet")
    con.execute(f"CREATE VIEW signals AS SELECT * FROM read_parquet('{glob}')")
    rel = con.execute(sql)
    cols = [d[0] for d in rel.description]
    rows = [dict(zip(cols, r)) for r in rel.fetchmany(500)]   # cap at 500 rows
    return rows
```

Key safety measures:
- `_is_readonly_sql()` rejects anything not starting with `SELECT` or `WITH`, and scans for `INSERT / UPDATE / DELETE / DROP / CREATE TABLE / ATTACH` — prevents the model from accidentally mutating state
- Fresh `":memory:"` connection per call — no state leaks between tool calls
- `fetchmany(500)` — prevents a huge result set from flooding the model context

### `read_file(path: str) → str`

```python
def read_file(self, path: str, max_bytes: int = 20_000) -> str:
    target = _confine(self.repo, path)    # security: path must stay inside repo root
    return target.read_text(errors="replace")[:max_bytes]
```

`_confine()` resolves the path and checks `target.is_relative_to(root)`. This prevents path traversal: `read_file("../../.env")` raises `ToolError`. The model can only read files inside `REPO_PATH`.

### `grep(pattern: str, glob: str = "") → list[str]`

```python
def grep(self, pattern: str, glob: str = "") -> list[str]:
    cmd = ["git", "grep", "-n", "-I", "-e", pattern]   # -n = line numbers, -I = skip binaries
    if glob:
        cmd += ["--", glob]
    return self._git(cmd, allow_empty=True).splitlines()[:200]
```

Uses `git grep` not stdlib `grep` — it's faster (uses git's index), respects `.gitignore`, and skips binary files automatically (`-I`). Returns at most 200 matches to stay within context limits.

### `git_log(path: str = "", limit: int = 20) → str`

```python
cmd = ["git", "log", f"-{limit}", "--pretty=%h %ad %an %s", "--date=short"]
if path:
    cmd += ["--", path]
```

The `--pretty` format gives `abc1234 2026-09-28 Alice fix null check in billing`. The optional `path` filters to commits that touched a specific file — essential for "did this file change recently and is that why we're seeing errors?"

### `git_blame(path, start, end) → str`

```python
return self._git(["git", "blame", "-L", f"{start},{end}", "--", path])
```

`-L start,end` blames only the specified line range — keeps the output small. The model uses this to answer "who changed line 42 of charge.py and when?" before proposing a fix, so the fix is attributed correctly.

### `git_diff(from_ref, to_ref="HEAD", path="") → str`

```python
cmd = ["git", "diff", f"{from_ref}..{to_ref}"]
if path:
    cmd += ["--", path]
return self._git(cmd)[:20_000]
```

Useful for "what changed between the last deploy and HEAD?" The model often calls this with the last release tag as `from_ref` to pinpoint regression-introducing commits.

### `list_dir(path: str = "") → list[str]`

```python
entries = sorted(target.iterdir())
return [
    str(e.relative_to(self.repo)) + ("/" if e.is_dir() else "")
    for e in entries
    if not e.name.startswith((".", "__pycache__"))
]
```

Returns paths with trailing `/` for directories — e.g. `["app/", "tests/", "pyproject.toml"]`. The model calls this when the file tree doesn't go deep enough. It's the navigation tool before `read_file` or `grep`.

### `repo_file_tree(max_files=80) → str`

Injected into the system prompt (not a callable tool). Walks the repo with `rglob("*")`, filters to source extensions (`.py .ts .tsx .js .go .java .rb .rs`), skips `node_modules / .git / __pycache__ / .venv / dist / build / .next`. Returns up to 80 paths as a flat list. This is injected at the start of every run so the model never guesses file locations.

### `append_memory(name: str, content: str) → str`

```python
safe = "".join(c for c in name if c.isalnum() or c in "-_") or "note"
path = self.settings.memory_dir / f"{safe}.md"
path.write_text(content)
```

The agent calls this at the end of an investigation to record what it learned. On the next run, `read_memory()` loads all `.md` files in `memory/` into the system prompt. Over time, this makes each run cheaper and faster — the model doesn't re-derive facts it already knows.

### Security: path confinement

```python
def _confine(root: Path, rel: str) -> Path:
    root   = root.resolve()
    target = (root / rel).resolve()         # resolve() follows symlinks
    if not target.is_relative_to(root):
        raise ToolError(f"path '{rel}' escapes the allowed root")
    return target
```

Calling `resolve()` on both paths before comparison handles symlinks, `../` components, and absolute path injection (`/etc/passwd`). The model is structurally unable to read files outside `REPO_PATH`.

---

## 10. The Warehouse (DuckDB)

Source: `openclaw/warehouse.py`

The warehouse aggregates all historical Parquet snapshots into a single queryable file: `stages/04_warehouse/openclaw.duckdb`.

### Why a persistent warehouse on top of raw Parquet?

The raw snapshots are per-run Parquet files. Querying across 30 days of runs means reading 30+ files per query. The warehouse pre-aggregates into mart tables so BI tools (Power BI, Looker Studio) get fast answers with no DuckDB knowledge required.

### The three mart tables

**`mart_signals`** — deduplicated fact table (grain: one signal)
```sql
SELECT DISTINCT source, kind, severity,
    CASE severity WHEN 'critical' THEN 4 WHEN 'error' THEN 3 WHEN 'warn' THEN 2 ELSE 1 END AS severity_rank,
    ts::TIMESTAMPTZ       AS ts,
    ts::DATE              AS event_date,
    date_trunc('hour', ts) AS event_hour,
    entity, message
FROM read_parquet('snapshots/**/*.parquet', hive_partitioning=false)
WHERE source IS NOT NULL
```
The `DISTINCT` deduplicates — if you run the same connector twice in one day you won't double-count signals.

**`mart_hourly`** — errors per source per hour (for line charts)
```sql
SELECT event_date, event_hour, source,
    COUNT(*)                                    AS total_signals,
    SUM(CASE WHEN severity_rank >= 3 THEN 1 END) AS errors,
    SUM(CASE WHEN severity_rank = 4  THEN 1 END) AS criticals,
    COUNT(DISTINCT entity)                      AS unique_entities,
    ROUND(errors * 100.0 / COUNT(*), 2)         AS error_rate_pct
FROM mart_signals GROUP BY 1,2,3
```

**`mart_top_entities`** — top error-prone services (for bar charts)
```sql
SELECT source, entity,
    COUNT(*) AS total_signals,
    SUM(...)  AS errors,
    SUM(...)  AS criticals,
    MIN(ts)   AS first_seen,
    MAX(ts)   AS last_seen
FROM mart_signals WHERE severity_rank >= 2
GROUP BY source, entity
ORDER BY errors DESC
```

### Connecting Power BI

1. Download DuckDB ODBC driver from duckdb.org
2. Power BI Desktop → Get Data → ODBC → `Driver={DuckDB Driver};Database=<full path to openclaw.duckdb>`
3. Select `mart_hourly`, `mart_top_entities`
4. Build visuals: line chart (`event_hour` x `error_rate_pct`, breakdown by `source`), bar chart (`entity` x `errors`)

Or use the CSV exports (no ODBC needed):
```bash
openclaw build-db --export-csv
# writes: stages/04_warehouse/exports/mart_signals.csv
#                                      mart_hourly.csv
#                                      mart_top_entities.csv
```

---

## 11. Every File Explained

| File | Role | Key exports |
|---|---|---|
| `cli.py` | Typer CLI — the entry point; wires all commands | `app` (Typer), `parse_window()` |
| `config.py` | Reads `.env` via pydantic-settings into a typed `Settings` object; `lru_cache` so it's loaded once | `Settings`, `get_settings()` |
| `models.py` | The `Signal` dataclass — universal row type every connector produces | `Signal` |
| `runner.py` | Orchestrates `build_connectors → new_run_dir → fetch → write_snapshot → run_analysis`; shared by CLI and API | `execute()` |
| `snapshot.py` | Converts `list[Signal]` → pyarrow Table → Parquet file (zstd); declares the shared schema | `write_snapshot()`, `new_run_dir()`, `SCHEMA` |
| `analyze.py` | Builds system prompt (injecting rules/memory/file tree), assembles tool schemas, calls Jev then the tool loop | `run_analysis()`, `TOOL_SCHEMAS`, `SYSTEM` |
| `llm.py` | Raw OpenAI Responses API loop — `previous_response_id` threading, tool dispatch, 32-iteration cap | `run_tool_loop()` |
| `tools.py` | All agent tools as plain Python methods; path confinement; read-only SQL guard | `Tools` class, `_confine()`, `_is_readonly_sql()` |
| `jev.py` | System-1 triage: DuckDB → 50 clusters → cheap model → top 8 by urgency | `prerank()`, `cluster_summary()` |
| `report.py` | Saves the final markdown to `<run_dir>/report.md` | `save_report()` |
| `apply.py` | Creates a git branch, writes the proposed fix files, commits; only runs with `--apply` | `FixWriter` |
| `warehouse.py` | Reads all `snapshots/**/*.parquet` into DuckDB; builds 3 mart tables; optional CSV export | `build()`, `query()` |
| `analytics.py` | Non-LLM stats: time series, anomaly detection, log clustering (for `openclaw stats`) | `summary()`, `render_summary()` |
| `archive.py` | Uploads old snapshots to Cloudflare R2 (S3-compatible) via boto3 | `archive_old()` |
| `api.py` | FastAPI app — lists runs, serves reports, triggers runs via HTTP | `app` (FastAPI) |
| `connectors/base.py` | Abstract `Connector` class + `REGISTRY` dict + `build_connectors()` factory | `Connector`, `REGISTRY` |
| `connectors/_util.py` | Shared HTTP session helpers (retries, timeouts) | `cf_get()`, `cf_post()` |

---

## 12. Data Flow — End to End

```
User types: openclaw run --since 7d --sources sentry,cloudwatch_logs
                │
                ▼
        cli.py: parse_window("7d") → timedelta(days=7)
        cli.py: runner.execute(settings, window, ["sentry","cloudwatch_logs"])
                │
                ▼
        runner.py: build_connectors(["sentry","cloudwatch_logs"], settings)
                │    → SentryConnector(settings)
                │    → CloudWatchLogsConnector(settings)
                │
                ▼
        runner.py: new_run_dir(settings.snapshots_dir)
                │    → mkdir snapshots/20260928T091335Z/
                │
                ├─► SentryConnector.fetch(timedelta(days=7))
                │       → sentry.io API calls (per project)
                │       → normalize each event → Signal(source="sentry", ...)
                │       → return list[Signal]
                │   write_snapshot(signals, run_dir, "sentry")
                │       → pa.Table.from_pylist(rows, schema=SCHEMA)
                │       → pq.write_table(table, "snapshots/.../sentry.parquet", compression="zstd")
                │
                ├─► CloudWatchLogsConnector.fetch(timedelta(days=7))
                │       → describe_log_groups("/aws/apprunner/")
                │       → filter_log_events(group, startTime, filterPattern)
                │       → normalize each line → Signal(source="cloudwatch_logs", ...)
                │   write_snapshot(signals, run_dir, "cloudwatch_logs")
                │
                ▼
        analyze.py: run_analysis(run_dir, settings)
                │
                ├─► jev.cluster_summary(tools)
                │       → duckdb_query("SELECT source,entity,severity,count(*) ...")
                │       → returns 50 clusters
                │
                ├─► jev.prerank(settings, clusters)
                │       → gpt-4o-mini via OpenRouter: rank by urgency
                │       → returns top 8 clusters
                │
                ├─► build system prompt
                │       → inject repo_file_tree() (real source paths)
                │       → inject read_rules() (your runbooks)
                │       → inject read_memory() (past learnings)
                │       → inject focus clusters (from Jev)
                │
                ▼
        llm.py: run_tool_loop(client, model, system, user, tools, dispatch)
                │
                │  iter 1: model calls duckdb_query("SELECT severity, count(*) ...")
                │           → result fed back via function_call_output
                │  iter 2: model calls duckdb_query("SELECT entity, message ...")
                │  iter 3: model calls list_dir("app/services/")
                │  iter 4: model calls read_file("app/services/job_queue.py")
                │  iter 5: model calls git_blame("app/services/job_queue.py", 1450, 1480)
                │  iter 6: model calls append_memory("dead-letter-logic", "...")
                │  iter 7: model returns final Markdown (no tool calls)
                │
                ▼
        report.py: save_report(run_dir, text)
                → writes snapshots/20260928T091335Z/report.md

        (output printed to terminal)
```

---

## 13. PR & Interview Questions

### Architecture

**Q: Why Parquet instead of a real database like Postgres or SQLite?**

Parquet is columnar — DuckDB can read just the columns it needs without loading the full row. For log analysis, you almost always query `severity` and `source` but rarely `raw_json`. A row-oriented store reads all columns on every scan. Parquet also compresses 5–10× better than CSV or SQLite for repetitive string values. The key insight: Parquet + DuckDB gives you SQL with zero operational overhead — no server, no migrations, files you can `cp` or `rm`.

**Q: What does `previous_response_id` in the Responses API do and why does it matter?**

It tells the OpenAI server to thread this request as a continuation of a prior one. The server keeps the conversation history on its side — you only send new `function_call_output` items each turn instead of the full history. Without it, a 10-turn investigation would resend the entire growing context on every iteration, making cost grow quadratically with tool calls. With it, cost is roughly constant per turn.

**Q: Why does the system prompt inject a repo file tree instead of letting the model use `list_dir` to discover paths?**

Without the upfront tree, the model's first action on every run is a `list_dir` call to orient itself — wasting one of the 32 iterations and adding latency. The tree is cheap to generate (a `rglob` walk on a local filesystem) and collapses path discovery into zero turns. The model still has `list_dir` for when it needs to go deeper than the top-80-file snapshot.

**Q: What prevents the agent from reading your `.env` file or other secrets?**

`_confine()` in `tools.py`. It resolves both the root and the target path (handling symlinks and `../` traversal) and checks `target.is_relative_to(root)`. The root is `REPO_PATH` (your backend codebase), not the OpenClaw directory. Even if the model asks for `../../openclaw/.env`, the resolved absolute path doesn't start with `REPO_PATH`, so the call raises a `ToolError` that gets returned to the model as an error string.

**Q: How does the Jev pre-ranker reduce cost?**

It converts up to 10,000 raw signal rows into at most 50 cluster summaries using DuckDB (local, free). It then sends those 50 one-line summaries to a cheap model (~$0.0001) to get importance ordering. The expensive reasoning model then only receives the top 8 clusters (~200 tokens of focus context) instead of thousands of raw log lines. In practice this cuts the expensive model's token consumption by 70–90% on large snapshots.

**Q: Why use structlog instead of the standard library `logging`?**

`structlog` emits JSON by default — every log event is a machine-readable dict (`{"event": "tool_call", "name": "duckdb_query", "iteration": 3, ...}`). This means you can pipe `openclaw run` output through `jq` or ingest it into any log aggregator without parsing. Standard library logging produces human strings that require regex to parse.

### Code design

**Q: Why is `get_settings()` wrapped in `@lru_cache`?**

`Settings()` calls pydantic-settings which reads and validates `.env` on every instantiation. If multiple connectors called `get_settings()` directly, `.env` would be read 16 times per run. `lru_cache` makes it a singleton — parsed once, reused everywhere. Note: this means changing `.env` mid-process has no effect (fine for a CLI tool; would be a bug in a long-running server).

**Q: Why are tools plain methods on a class instead of functions passed as lambdas?**

Unit testability. The `Tools` class can be instantiated with a test `run_dir` pointing at a fixture Parquet file and a test `repo_path`. No mock is needed — you call `tools.duckdb_query("SELECT count(*) FROM signals")` directly and assert on the result. The lambda dispatch in `analyze.py` is a thin adapter on top of these plain methods.

**Q: What happens if a connector raises an exception?**

`runner.py` doesn't catch connector exceptions — they propagate and abort the run. The design choice is intentional: a failing connector means something is wrong with your credentials or the API is down, and you want to know, not silently get zero signals. The exception boundary is the connector's `fetch()` method itself — if the API is unavailable, `fetch()` should catch that specific error, log a warning, and return `[]`. This separates "connector is misconfigured" (hard fail) from "connector is temporarily unavailable" (soft fail, return empty).

**Q: How does `write_fix` work and why is it gated behind `--apply`?**

`apply.py`'s `FixWriter` creates a git branch named `openclaw-fix-<run_id>`, writes the new file content, and commits. This branch is never pushed — it only exists locally. The user must explicitly run `git push` and open a PR themselves. `write_fix` is only added to the tool schema when `--apply` is passed; otherwise the tool doesn't exist in the schema, so the model cannot call it even if it tries.

**Q: What is the `_is_readonly_sql` guard and why is it not enough on its own?**

It checks that the query starts with `SELECT` or `WITH` and doesn't contain mutation keywords. But it's string-matching — a clever query like `SELECT * FROM (DELETE FROM signals RETURNING *)` would not be caught. The deeper defense is that `duckdb.connect(":memory:")` creates a fresh in-memory database with no persistent state. Even if a mutation got through, it would only affect the ephemeral connection, not the Parquet files on disk. The guard is a first line of defence; the in-memory connection is the real backstop.

### Data engineering

**Q: What is columnar storage and why does it matter for this use case?**

In row-oriented storage (Postgres, SQLite), a row's data lives contiguously on disk: `[source, kind, severity, ts, entity, message, raw_json]` for each row. To read `severity` from 10,000 rows you read 10,000 full rows. In columnar storage (Parquet), all `severity` values for all rows live contiguously: `[error, warn, error, info, ...]`. To read `severity` from 10,000 rows you read only the `severity` column. For analytics (`GROUP BY severity, COUNT(*)`) this is 6× less I/O on a 7-column table.

**Q: What does ZSTD compression do to the Parquet file?**

ZSTD (Zstandard) is a modern LZ77-family compression algorithm. For log data with highly repetitive strings (`source="sentry"` appears in every row), ZSTD achieves 5–10× compression versus uncompressed. It's also faster to decompress than gzip. DuckDB decompresses columns in parallel before scanning — the compression overhead is invisible for interactive queries.

**Q: Why does `mart_signals` use `DISTINCT` but the raw connector output doesn't deduplicate?**

Connectors run on time windows. If you run `--since 7d` today and again tomorrow, the last 6 days overlap. Without `DISTINCT`, `build-db` would double-count those signals. Using `DISTINCT` at warehouse build time means the raw Parquet files remain immutable append-only artifacts — you never modify them — and deduplication happens in the aggregation layer where it's cheap (DuckDB hash aggregate).

**Q: What is `severity_rank` in the warehouse and why not just `ORDER BY severity`?**

`severity` is a string — `ORDER BY severity` sorts alphabetically (`critical < error < info < warn`), which is wrong. `severity_rank` maps `critical→4, error→3, warn→2, info→1` so `WHERE severity_rank >= 3` correctly means "errors and above". This is a common data modelling pattern: store the human-readable label for display, store the integer rank for computation.
