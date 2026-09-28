# Scheduling runs

OpenClaw is deliberately **on-demand with no built-in daemon** — you trigger runs,
nothing runs in the background on its own. To run it on a schedule, drive the CLI
from an **external scheduler**. Two supported patterns:

## 1. GitHub Actions (recommended)

A ready-to-use workflow lives at
[`.github/workflows/openclaw-scheduled.yml`](../.github/workflows/openclaw-scheduled.yml).
It runs on a cron schedule (and on manual dispatch), snapshots the configured sources,
analyzes them, and uploads the report as a build artifact.

Setup:
1. Add repository **secrets** (Settings → Secrets and variables → Actions) for whatever
   you use: `OPENAI_API_KEY`, `OPENAI_MODEL`, `LOKI_URL`, `CLOUDFLARE_API_TOKEN`,
   `CLOUDFLARE_ZONE_ID`, `SENTRY_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`, etc.
2. Edit the `--since` / `--sources` in the workflow to match your stack.
3. If the agent should read your service code, the workflow checks out a second repo
   into `service/` and sets `REPO_PATH=service` — point it at your repo.

Download the report from the run's **Artifacts**, or extend the workflow to post it to
Slack / open an issue.

## 2. cron / systemd (self-hosted)

Run the CLI from crontab on any host that has the venv and `.env`:

```cron
# every day at 08:00 — snapshot + analyze, log the report
0 8 * * *  cd /opt/openclaw && . .venv/bin/activate && openclaw run --since 24h --sources loki,cloudflare,sentry >> /var/log/openclaw.log 2>&1
```

Pair it with archival so local snapshots don't accumulate:

```cron
# weekly — push snapshots older than 30d to R2 and free local disk
0 3 * * 0  cd /opt/openclaw && . .venv/bin/activate && openclaw archive --older-than 30d --delete-local
```

A `systemd` timer works the same way — a `oneshot` service running `openclaw run`, fired
by a `.timer`.

## Why not an in-process scheduler?

Keeping scheduling external (cron/Actions/systemd) means no long-lived process to
babysit, native retries/logs/alerting from the scheduler, and the same one-command
behavior locally and in CI. It matches OpenClaw's on-demand design.
