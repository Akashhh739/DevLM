"""Kubernetes events connector (event-like). Needs the kubernetes client ([k8s] extra).

Keeps only Warning events (skips Normal noise) and escalates known-bad reasons to error.
Gated on KUBE_ENABLED so it never auto-activates against whatever kubeconfig is present.
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from openclaw.config import Settings
from openclaw.connectors._util import parse_ts
from openclaw.connectors.base import register
from openclaw.models import Signal

log = structlog.get_logger()

_ERROR_REASONS = {
    "Failed", "BackOff", "Unhealthy", "OOMKilling", "FailedMount",
    "FailedScheduling", "CrashLoopBackOff", "FailedCreatePodSandBox", "Evicted",
}


def _parse_events(events: list[dict]) -> list[Signal]:
    out: list[Signal] = []
    for e in events:
        if e.get("type") == "Normal":
            continue  # keep only Warnings
        reason = e.get("reason", "")
        out.append(
            Signal(
                source="kubernetes",
                kind="k8s_event",
                severity="error" if reason in _ERROR_REASONS else "warn",
                ts=parse_ts(e.get("ts")),
                entity=e.get("object") or e.get("namespace") or "cluster",
                message=f"{reason}: {e.get('message', '')}",
                raw=e,
            )
        )
    return out


@register("kubernetes")
class KubernetesConnector:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.kube_enabled
        self.context = settings.kube_context

    def fetch(self, window: timedelta) -> list[Signal]:
        if not self.enabled:
            log.warning("kubernetes_skipped", reason="set KUBE_ENABLED=true")
            return []
        try:
            from kubernetes import client, config
        except ImportError:
            log.error("kubernetes_skipped", reason="pip install kubernetes")
            return []

        try:
            config.load_kube_config(context=self.context or None)
        except Exception:  # noqa: BLE001 - fall back to in-cluster config
            try:
                config.load_incluster_config()
            except Exception as exc:  # noqa: BLE001
                log.error("kubernetes_config_failed", error=str(exc))
                return []

        try:
            raw = client.CoreV1Api().list_event_for_all_namespaces(limit=500)
        except Exception as exc:  # noqa: BLE001
            log.error("kubernetes_fetch_failed", error=str(exc))
            return []

        events = [
            {
                "type": it.type,
                "reason": it.reason,
                "message": it.message,
                "object": getattr(it.involved_object, "name", ""),
                "namespace": it.metadata.namespace,
                "ts": it.last_timestamp.isoformat() if it.last_timestamp else None,
            }
            for it in raw.items
        ]
        signals = _parse_events(events)
        log.info("kubernetes_fetched", count=len(signals))
        return signals
