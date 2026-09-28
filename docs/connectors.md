# Connector setup

Every connector is **read-only** and **optional**. Leave a variable **blank** to skip
that connector — it returns zero signals and does not error.

```bash
cp .env.example .env   # then fill in only the sources you have
openclaw sources        # list all 14 registered connectors
openclaw run --sources demo --since 24h --snapshot-only   # test with no creds
```

Secrets stay host-side in the connector executors — **they are never sent to the model**.
All connectors use the minimum read-only scope needed. Nothing writes to your systems.

---

## demo — no setup, always works

Generates synthetic signals. Use it to prove the pipeline works before adding real creds.

```bash
openclaw run --sources demo --since 24h
```

No env vars needed.

---

## loki — Grafana Loki (raw log lines)

Pulls log lines matching `error|exception|failed|fatal|panic|crash` for the window.

| Env | Example | Notes |
|---|---|---|
| `LOKI_URL` | `http://localhost:3100` | Base URL — required to enable |
| `LOKI_NAMESPACE` | `production` | Optional; omit to query all streams |

**Where to find:**
- Self-hosted: the address you're already using.
- Grafana Cloud: Grafana dashboard → Connections → Data sources → Loki → HTTP URL.

No auth token needed for unauthenticated Loki. If behind a gateway, expose a
read-only local tunnel (e.g. `kubectl port-forward`).

---

## cloudflare — WAF / firewall events (IP spam, blocked attacks)

Uses the Cloudflare GraphQL Analytics API (`firewallEventsAdaptive`).

| Env | Example | Notes |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | `abc123...` | Read-only token |
| `CLOUDFLARE_ZONE_ID` | `abcdef12...` | 32-char hex zone id |

**Where to find:**
1. `CLOUDFLARE_API_TOKEN` — [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) → **Create Token** → Custom token:
   - `Zone → Firewall Services : Read`
   - `Zone → Logs : Read`
   - `Account → Account Analytics : Read`
   - Scope to **your specific zone** (not all zones).
2. `CLOUDFLARE_ZONE_ID` — Cloudflare dashboard → click your domain → **Overview** → right sidebar under **Zone ID**.

---

## cf\_workers — Cloudflare Workers (invocation error rates)

Account-level Workers analytics. Flags scripts whose error rate ≥ 5%.

| Env | Example | Notes |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | *(same token as cloudflare above)* | Needs `Account Analytics:Read` |
| `CLOUDFLARE_ACCOUNT_ID` | `abcdef12...` | 32-char hex account id |

**Where to find:**
- `CLOUDFLARE_ACCOUNT_ID` — Cloudflare dashboard → any zone → **Overview** → right sidebar under **Account ID**.
- Same token as the `cloudflare` connector — no extra token needed.

---

## aws — GuardDuty findings + CloudWatch alarms

Lists active GuardDuty threat findings and any CloudWatch alarms in ALARM state.

**Requires:** `pip install -e ".[aws]"`

| Env | Example | Notes |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Required to enable the connector |
| `AWS_ACCESS_KEY_ID` | `AKIAIOSFODNN7...` | Or use a named profile / IAM role |
| `AWS_SECRET_ACCESS_KEY` | `wJalrX...` | Or use a named profile / IAM role |
| `AWS_PROFILE` | `openclaw-readonly` | Alternative to inline keys |

**Permissions — create a read-only IAM policy with:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "guardduty:ListDetectors",
        "guardduty:ListFindings",
        "guardduty:GetFindings",
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "*"
    }
  ]
}
```
Or attach the AWS managed policy **SecurityAudit** (broader but fully read-only).

**Where to get keys:**
- AWS Console → IAM → Users → your user → **Security credentials** → **Create access key**.
- Prefer a named profile (`~/.aws/credentials`) or an assumed role over long-lived inline keys.

---

## apprunner — AWS App Runner service health

Lists all App Runner services; flags any not in `RUNNING` state.

**Requires:** `pip install -e ".[aws]"` (same as `aws` connector)

Reuses `AWS_REGION` + AWS credentials — no extra env vars.

---

## github — CI workflow failures

Reads GitHub Actions workflow runs; keeps non-successful conclusions (failure, cancelled, timed\_out).

| Env | Example | Notes |
|---|---|---|
| `GITHUB_TOKEN` | `github_pat_11AA...` | Fine-grained PAT |
| `GITHUB_REPO` | `your-org/your-repo` | `owner/repo` format |

**Where to get:**
1. github.com → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Repository permissions needed (read-only):
   - **Actions**: Read
   - **Contents**: Read
   - **Metadata**: Read (automatically included)
3. Set **Resource owner** to the org that owns the repo.

Classic PAT alternative: scope `repo` (read) + `workflow`.

---

## supabase — auth audit log (failed logins, suspicious actions)

Reads `auth.audit_log_entries`; flags `login_failed`, `token_revoked`, `user_banned`, etc.

**Requires:** `pip install -e ".[postgres]"`

| Env | Example | Notes |
|---|---|---|
| `SUPABASE_DB_URL` | `postgresql://openclaw_ro:pass@db.xxx.supabase.co:5432/postgres` | Read-only Postgres connection string |

**Where to find connection string:**
Supabase dashboard → **Project Settings** → **Database** → **Connection string** → URI tab.
Use port **5432** (direct) or **6543** (connection pooler — better for production).

**Minimal read-only role (run once in your Supabase SQL editor):**
```sql
CREATE ROLE openclaw_ro WITH LOGIN PASSWORD 'choose_a_strong_password';
GRANT CONNECT ON DATABASE postgres TO openclaw_ro;
GRANT USAGE ON SCHEMA auth TO openclaw_ro;
GRANT SELECT ON auth.audit_log_entries TO openclaw_ro;
```
Then use `postgresql://openclaw_ro:choose_a_strong_password@db.xxx.supabase.co:5432/postgres`.

---

## postgres — database health (threshold archetype)

Runs read-only SELECTs against `pg_stat_*` views. Emits a signal when a metric
crosses a threshold: connection saturation, long-running queries, deadlocks, low cache-hit ratio.

**Requires:** `pip install -e ".[postgres]"`

| Env | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://watchman_ro:pass@localhost:5432/mydb` | Read-only monitoring role |

**Minimal read-only role:**
```sql
CREATE ROLE watchman_ro WITH LOGIN PASSWORD 'choose_a_strong_password';
GRANT pg_monitor TO watchman_ro;   -- gives access to all pg_stat_* views, no table data
```

Thresholds: edit `openclaw/connectors/postgres.py` (`CONN_WARN`, `LONG_QUERY_ERROR`, etc.).

---

## sentry — unresolved error issues

Reads the Issues API; returns issues that are unresolved in the given time window.

| Env | Example | Notes |
|---|---|---|
| `SENTRY_TOKEN` | `sntrys_eyJpYXQ...` | Auth token with read-only scopes |
| `SENTRY_ORG` | `my-org` | Org slug (the URL segment: sentry.io/**my-org**/) |
| `SENTRY_PROJECT` | `backend` | Project slug |
| `SENTRY_URL` | *(blank)* | Only set for self-hosted Sentry; default is `https://sentry.io` |

**Where to get token:**
sentry.io → **Settings** → **Auth Tokens** → **Create New Token**.
Scopes needed: `event:read`, `project:read`, `org:read`.

**How to find slugs:**
- Org slug: the segment after `sentry.io/` in your Sentry URL.
- Project slug: the segment after the org slug — `sentry.io/<org>/<project>/`.

---

## prometheus — firing alerts

Reads `/api/v1/alerts`; ingests currently-firing alerts only (thresholds are already
applied by your Prometheus rules — this just reads the results).

| Env | Example | Notes |
|---|---|---|
| `PROMETHEUS_URL` | `http://localhost:9090` | Required to enable |

No token needed for unauthenticated Prometheus. If behind auth, port-forward or expose
a read-only local tunnel.

---

## kubernetes — cluster events (CrashLoopBackOff, OOMKilled, etc.)

Reads Kubernetes `Warning` events; escalates known-bad reasons (BackOff, OOMKilling,
FailedScheduling, Evicted) to `error` severity.

**Requires:** `pip install -e ".[k8s]"`

| Env | Example | Notes |
|---|---|---|
| `KUBE_ENABLED` | `true` | Must be explicitly set to `true` to enable |
| `KUBE_CONTEXT` | `my-cluster` | Optional; uses current context in `~/.kube/config` if blank |

**Why the explicit flag?** Without it, the connector would silently connect to whatever
kubeconfig is active, which could be the wrong cluster.

**Minimal RBAC (apply once):**
```yaml
# openclaw-rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: openclaw-view
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view          # built-in read-only role
subjects:
  - kind: ServiceAccount
    name: openclaw
    namespace: default
```
```bash
kubectl apply -f openclaw-rbac.yaml
```
Or just use your existing kubeconfig — the connector only calls `list events`.

---

## dns — DNS resolution checks (no credentials)

Resolves each hostname using the stdlib resolver; flags any that fail to resolve.
Useful for catching misconfigured DNS after deploys or zone changes.

| Env | Example | Notes |
|---|---|---|
| `DNS_HOSTS` | `api.example.com,www.example.com,auth.example.com` | Comma-separated |

No credentials. No external API. Uses your machine's DNS resolver.

---

## ga4 — Google Analytics 4 (traffic anomalies, session drops)

Reads sessions and users for the window; emits a `warn` signal when sessions drop
below 50% of the window average.

**Requires:** `pip install -e ".[ga4]"`

| Env | Example | Notes |
|---|---|---|
| `GA4_PROPERTY_ID` | `123456789` | Numeric property ID (not the measurement ID) |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/path/to/sa-key.json` | Standard Google env var |

**Setup (one time):**
1. [console.cloud.google.com](https://console.cloud.google.com) → **IAM & Admin** → **Service Accounts** → **Create Service Account**.
2. No IAM roles needed at the project level.
3. **Keys** tab → **Add Key** → JSON → download the file.
4. GA4 dashboard → **Admin** → **Property Access Management** → **Add** → paste the service account email (shown in the JSON as `client_email`) → role: **Viewer**.
5. **Property ID**: GA4 Admin → **Property Settings** → the number next to "Property ID".

---

## Optional add-ons (not connectors)

### LangSmith — trace every run

Records every tool call, token count, and cost. Runs appear at
[smith.langchain.com](https://smith.langchain.com) automatically once configured.

**Requires:** `pip install -e ".[trace]"`

| Env | Example | Notes |
|---|---|---|
| `LANGSMITH_API_KEY` | `lsv2_pt_...` | From smith.langchain.com → Settings → API Keys |
| `LANGSMITH_PROJECT` | `openclaw` | Logical project grouping in LangSmith |

---

### Jev pre-ranking — System-1 triage before GPT

Ranks which log clusters matter before sending them to the expensive model.
No-op (silently skipped) if `JEV_API_KEY` is blank.

| Env | Default |
|---|---|
| `JEV_API_KEY` | *(blank = disabled)* |
| `JEV_BASE_URL` | `https://openrouter.ai/api/v1` |
| `JEV_MODEL` | `typesafe/jev-1.13` |

Key at [openrouter.ai](https://openrouter.ai) → **Keys** → Create (free tier available).

---

### R2 cold archive — store old snapshots cheaply

Uploads Parquet snapshots older than a threshold to Cloudflare R2 (S3-compatible).

**Requires:** `pip install -e ".[archive]"`

| Env | Example | Notes |
|---|---|---|
| `R2_ACCOUNT_ID` | `abcdef12...` | Cloudflare account id |
| `R2_ACCESS_KEY_ID` | `abc...` | R2 API token key |
| `R2_SECRET_ACCESS_KEY` | `xyz...` | R2 API token secret |
| `R2_BUCKET` | `openclaw-snapshots` | Your R2 bucket name |
| `R2_ENDPOINT` | *(blank)* | Auto-derived: `https://<account>.r2.cloudflarestorage.com` |

**Setup:** Cloudflare dashboard → **R2** → **Create bucket** → **Manage R2 API tokens** → Create token with **Object Read & Write** scoped to your bucket.

**Usage:**
```bash
openclaw archive --older-than 30d            # upload, keep local copies
openclaw archive --older-than 30d --delete-local   # upload then delete local
```
