"""Runtime configuration, loaded from environment / .env.

Secrets (API keys, tokens) live here and in connector executors only — never in
anything sent to the model.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- OpenAI (model + key provided by the user later) ---
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="", alias="OPENAI_MODEL")

    # --- Loki ---
    loki_url: str = Field(default="http://localhost:3100", alias="LOKI_URL")
    loki_namespace: str = Field(default="", alias="LOKI_NAMESPACE")

    # --- Cloudflare (read-only token: Analytics:Read, Firewall Services:Read) ---
    cloudflare_api_token: str = Field(default="", alias="CLOUDFLARE_API_TOKEN")
    cloudflare_zone_id: str = Field(default="", alias="CLOUDFLARE_ZONE_ID")
    cloudflare_account_id: str = Field(default="", alias="CLOUDFLARE_ACCOUNT_ID")  # for Workers

    # --- Prometheus (reads /api/v1/alerts) ---
    prometheus_url: str = Field(default="", alias="PROMETHEUS_URL")

    # --- Kubernetes events (uses local kubeconfig / in-cluster) ---
    kube_enabled: bool = Field(default=False, alias="KUBE_ENABLED")
    kube_context: str = Field(default="", alias="KUBE_CONTEXT")

    # --- GitHub (CI runs; read-only token + owner/repo) ---
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_repo: str = Field(default="", alias="GITHUB_REPO")

    # --- Supabase (auth audit log via its Postgres) ---
    supabase_db_url: str = Field(default="", alias="SUPABASE_DB_URL")

    # --- Sentry (auth token: event:read, project:read) ---
    sentry_token: str = Field(default="", alias="SENTRY_TOKEN")
    sentry_org: str = Field(default="", alias="SENTRY_ORG")
    sentry_project: str = Field(default="", alias="SENTRY_PROJECT")
    sentry_url: str = Field(default="https://sentry.io", alias="SENTRY_URL")

    # --- AWS (read-only IAM: GuardDuty + CloudWatch read). Creds via standard AWS env. ---
    aws_region: str = Field(default="", alias="AWS_REGION")
    # Extra CloudWatch log groups beyond auto-discovered /aws/apprunner/*/application
    cloudwatch_log_groups: str = Field(default="", alias="CLOUDWATCH_LOG_GROUPS")

    # --- PostgreSQL health (read-only monitoring connection string) ---
    database_url: str = Field(default="", alias="DATABASE_URL")

    # --- DNS (hosts to resolve; flags resolution failures) ---
    dns_hosts: str = Field(default="", alias="DNS_HOSTS")  # comma-separated

    # --- Google Analytics 4 (read-only service account via GOOGLE_APPLICATION_CREDENTIALS) ---
    ga4_property_id: str = Field(default="", alias="GA4_PROPERTY_ID")

    # --- Jev (optional triage/rank; TypeSafe via OpenRouter or native) ---
    jev_api_key: str = Field(default="", alias="JEV_API_KEY")
    jev_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="JEV_BASE_URL")
    jev_model: str = Field(default="typesafe/jev-1.13", alias="JEV_MODEL")

    # --- R2 cold archive (Cloudflare R2, S3-compatible) ---
    r2_account_id: str = Field(default="", alias="R2_ACCOUNT_ID")
    r2_access_key_id: str = Field(default="", alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field(default="", alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str = Field(default="", alias="R2_BUCKET")
    r2_endpoint: str = Field(default="", alias="R2_ENDPOINT")  # defaults from account id

    def r2_endpoint_url(self) -> str:
        return self.r2_endpoint or f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    # --- local paths the agent works against ---
    repo_path: Path = Field(default=Path("."), alias="REPO_PATH")
    rules_dir: Path = Field(default=Path("rules"), alias="RULES_DIR")
    memory_dir: Path = Field(default=Path("memory"), alias="MEMORY_DIR")
    snapshots_dir: Path = Field(default=Path("snapshots"), alias="SNAPSHOTS_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()
