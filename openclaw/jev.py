"""Optional Jev pre-ranking (System-1 triage).

Groups the snapshot into clusters and, when a Jev key is configured, asks Jev to
rank which clusters matter — so the expensive reasoning model only focuses on the
top few (cost control). Without a key it is a no-op: clusters stay ordered by volume.

Jev (TypeSafe AI) returns typed decisions rather than text; the call below is
best-effort and defensive — any failure falls back to the volume ordering, so the
pipeline never depends on it.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from openclaw.config import Settings
from openclaw.tools import Tools

log = structlog.get_logger()

_CLUSTER_SQL = (
    "SELECT source, entity, severity, count(*) AS n, min(message) AS sample "
    "FROM signals GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 50"
)


def cluster_summary(tools: Tools) -> list[dict[str, Any]]:
    """Group the snapshot into (source, entity, severity) clusters, busiest first."""
    return tools.duckdb_query(_CLUSTER_SQL)


def prerank(settings: Settings, clusters: list[dict[str, Any]], top: int = 8) -> list[dict[str, Any]]:
    """Return the clusters worth investigating, most-important first.

    No key -> volume ordering (already sorted). With a key -> Jev importance scores.
    """
    if not settings.jev_api_key or not clusters:
        return clusters[:top]
    try:
        from openai import OpenAI  # OpenRouter exposes an OpenAI-compatible surface

        client = OpenAI(api_key=settings.jev_api_key, base_url=settings.jev_base_url)
        listing = "\n".join(
            f"{i}: {c['severity']} {c['source']}/{c['entity']} n={c['n']} :: {c['sample']}"
            for i, c in enumerate(clusters)
        )
        resp = client.chat.completions.create(
            model=settings.jev_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a triage assistant. Respond ONLY with a JSON array of integers, no explanation, no markdown.",
                },
                {
                    "role": "user",
                    "content": "Rank these log clusters by how urgently a DevSecOps "
                    "engineer should investigate them. Return ONLY a JSON array of the "
                    f"cluster indices (integers), most important first.\n\n{listing}",
                },
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        # strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        order = json.loads(raw or "[]")
        ranked = [clusters[i] for i in order if isinstance(i, int) and 0 <= i < len(clusters)]
        if ranked:
            log.info("jev_preranked", kept=min(top, len(ranked)))
            return ranked[:top]
    except Exception as exc:  # noqa: BLE001 - never let triage break the run
        log.warning("jev_prerank_failed", error=str(exc))
    return clusters[:top]
