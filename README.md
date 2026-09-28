# OpenClaw

**On-demand DevSecOps analyst.** One command pulls logs from your platforms into a Parquet snapshot, then runs a GPT agent over the data + your local codebase to produce a grounded diagnosis with proposed code fixes.

No always-on daemon. No database. No queue. Every step is manual and auditable.

```bash
openclaw run --since 7d --sources sentry,cloudwatch_logs,postgres
```

```
## Summary
Found 2 critical issues in the billing service over the last 7 days.

## Findings

### 1. NullPointerError in charge.py (333 occurrences) — BUG
Evidence: sentry shows `NoneType has no attribute 'charge'` 333×.
Git blame shows the null-check was removed in commit abc1234 (2026-09-21, no test).
Fix: restore the guard at charge.py:42.

### 2. Storage sync misses (7,407 occurrences) — INTENTIONAL
Evidence: cloudwatch_logs shows `Storage sync download miss` for multiple users.
rules/storage.md documents this as expected behaviour on cache cold-start.
No action needed.
```

---

## How it works

```
$ openclaw run --since 7d --sources sentry,cloudwatch_logs
       │
       ▼
  1. COLLECT   Each connector pulls the time window from its API
               → normalizes every event to a Signal(source, kind, severity, ts, entity, message)
               → writes snapshots/<run-id>/<source>.parquet  (zstd, columnar)
       │
       ▼
  2. TRIAGE    Jev (fast cheap model) clusters signals by (source, entity, severity)
               → ranks the 50 clusters by urgency
               → top 8 focus areas fed to the expensive model  (cost control)
       │
       ▼
  3. ANALYZE   OpenAI Responses API tool-use loop (up to 32 iterations):
               ├─ duckdb_query   SQL over the Parquet snapshot  (view `signals`)
               ├─ read_file      read source files from your local codebase
               ├─ grep           git grep across the repo
               ├─ git_log        recent commits — did something change?
               ├─ git_blame      who/when/why changed a specific line
               ├─ git_diff       what changed between two refs
               ├─ list_dir       discover real paths before reading
               └─ append_memory  save learnings — next run starts smarter
       │
       ▼
  4. REPORT    Markdown: summary · findings (evidence + bug-vs-intentional + fix) · recommendations
               Saved to snapshots/<run-id>/report.md
```

---

## Features

- **16 connectors** — AWS App Runner, CloudWatch Logs, Cloudflare WAF/Workers/Pages, Sentry, Supabase, Postgres, DNS, GitHub Actions, Loki, Prometheus, GA4, Kubernetes, and a built-in demo
- **Parquet-native** — snapshots are zstd-compressed columnar files; inspect them yourself with DuckDB, pandas, or any Parquet-compatible tool
- **Code-aware** — the agent reads your local repo, runs `git blame` and `git diff`, and tells you *which commit introduced the bug* before proposing a fix
- **Compounding memory** — `rules/` you author (intended policy); `memory/` the agent grows (observed reality); repeat runs get faster and cheaper
- **Propose, don't act** — fixes are always proposals; `--apply` stages them on a local git branch for you to review before pushing
- **Warehouse** — `openclaw build-db` aggregates all snapshots into a DuckDB mart (hourly error rates, top entities, anomaly scores); connect Power BI or export CSVs
- **Optional tracing** — LangSmith integration logs every tool call, token count, and cost

---

## Connectors

| Source | What it pulls | Credentials |
|---|---|---|
| `cloudwatch_logs` | App Runner application logs (auto-discovered) | AWS key/secret |
| `apprunner` | Service health + deployment events | AWS key/secret |
| `aws` | GuardDuty findings + CloudWatch metrics | AWS key/secret |
| `sentry` | Error events + issue counts (multi-project) | Sentry auth token |
| `cloudflare` | WAF / firewall events | CF API token |
| `cf_workers` | Workers error rates + CPU time | CF API token |
| `cf_pages` | Pages deployment failures | CF API token (Pages:Read scope) |
| `supabase` | Auth audit log (failed logins) | Supabase DB URL |
| `postgres` | `pg_stat_*` — connections, long queries, deadlocks | Database URL |
| `dns` | DNS resolution success/failure | none — hosts list only |
| `github` | Failed Actions runs, open PRs | GitHub PAT |
| `loki` | Grafana Loki log queries | Loki URL |
| `prometheus` | Active alerts | Prometheus URL |
| `ga4` | Google Analytics 4 events | GA4 property + service account |
| `k8s` | Pod events, OOMKilled | kubeconfig |
| `demo` | Synthetic fixtures (no credentials) | none |

Unconfigured connectors are silently skipped — start with `demo` and add sources one at a time.

---

## Quick start

### 1. Install

```bash
git clone https://github.com/your-org/openclaw
cd openclaw
python -m venv .venv && source .venv/bin/activate

pip install -e .                        # core (demo + DNS work immediately)
pip install -e ".[aws]"                 # adds boto3 for AWS connectors
pip install -e ".[postgres]"            # adds psycopg for Postgres/Supabase
pip install -e ".[aws,postgres]"        # both
```

### 2. Configure

```bash
cp .env.example .env
```

Minimum to run the demo (no real credentials):

```env
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
REPO_PATH=/path/to/your/backend/repo
```

Add sources as you go — see [docs/connectors.md](docs/connectors.md) for every variable and required token scope.

### 3. Smoke test

```bash
openclaw run --sources demo --since 1d
```

### 4. Run against real sources

```bash
# 7 days from all configured connectors
openclaw run --since 7d --sources aws,cloudwatch_logs,sentry,postgres,dns

# snapshot only — check connector output before spending on GPT
openclaw run --since 7d --sources sentry --snapshot-only

# let the agent stage a fix on a local review branch
openclaw run --since 1d --sources cloudwatch_logs,sentry --apply
```

---

## CLI reference

```
openclaw run          Pull logs + analyze  (the main command)
openclaw sources      List all available connector names
openclaw stats        Time-series, anomaly, cluster analytics over a run  (no LLM)
openclaw build-db     Build / rebuild the DuckDB warehouse from all snapshots
openclaw archive      Upload old snapshots to Cloudflare R2 cold storage
openclaw serve        Launch dashboard at localhost:8000
```

### `openclaw run` flags

| Flag | Default | Description |
|---|---|---|
| `--since` | `1d` | Time window: `30m`, `24h`, `7d`, `30d` |
| `--sources` | `demo` | Comma-separated connector names |
| `--snapshot-only` | false | Skip analysis; write Parquet only |
| `--apply` | false | Let the agent stage fixes on a local branch |

---

## Querying snapshots directly

Snapshots are plain Parquet files — no special tooling required:

```python
import duckdb

# one run
duckdb.sql("SELECT severity, count(*) FROM read_parquet('snapshots/20260928T091335Z/*.parquet') GROUP BY 1")

# all runs
duckdb.sql("SELECT source, date_trunc('day', ts), count(*) FROM read_parquet('snapshots/**/*.parquet') GROUP BY 1,2 ORDER BY 2")
```

Or build the full warehouse and connect Power BI:

```bash
openclaw build-db --export-csv
# → stages/04_warehouse/openclaw.duckdb
# → stages/04_warehouse/exports/mart_hourly.csv
# → stages/04_warehouse/exports/mart_top_entities.csv
```

---

## Adding a runbook

Create `rules/my-service.md` (copy `rules/job-queue.example.md`). The agent reads every file in `rules/` at analysis time — so you can tell it what's intentional, what error strings to expect, and what the retry policy is. This prevents false positives.

---

## Memory — knowledge that compounds

After each run, the agent calls `append_memory(name, content)` to save what it learned to `memory/<name>.md`. On the next run, every file in `memory/` is loaded into the system prompt. Over time:

- The agent stops re-deriving the same facts
- Fewer tool calls → cheaper runs
- Diagnosis gets faster and more accurate

`rules/` = policy you author. `memory/` = observations the agent grows.

---

## Project layout

```
openclaw/
├── cli.py            entry point (Typer)
├── config.py         Settings — reads .env via pydantic-settings
├── models.py         Signal dataclass — the universal row every connector produces
├── runner.py         collect → snapshot → analyze pipeline
├── snapshot.py       Signal list → pyarrow Table → Parquet (zstd)
├── analyze.py        system prompt, tool schemas, run_analysis()
├── llm.py            OpenAI Responses API tool-loop (max 32 iterations)
├── tools.py          duckdb_query, read_file, grep, git_*, append_memory
├── jev.py            System-1 pre-ranker (cheap model clusters before expensive model)
├── report.py         saves report.md to the run directory
├── apply.py          --apply: stages fixes as a local git branch
├── warehouse.py      builds mart tables in openclaw.duckdb
├── analytics.py      time-series, anomaly, clustering (no LLM)
├── archive.py        cold-archive to Cloudflare R2
├── api.py            FastAPI dashboard
└── connectors/       one file per source, all implement Connector.fetch(window)
```

For a full technical deep-dive — how Parquet is written, how the tool loop works, how Jev pre-ranking saves cost, and 15 interview Q&As — see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Requirements

- Python 3.11+
- Git (for `git_blame` / `git_log` / `git_diff` tools)
- OpenAI API key (`gpt-4o-mini` works; swap model via `OPENAI_MODEL`)
- Local checkout of the codebase you want to analyze (`REPO_PATH`)

---

## License

MIT
