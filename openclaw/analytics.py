"""Analytics over a Parquet snapshot — classic data-science techniques.

Deliberately no LLM here: pandas + numpy + scikit-learn on the signals. These
functions are also exposed to the agent as tools, so the model can *call*
statistics instead of eyeballing raw logs.

Techniques (and what they teach):
- time-series resampling  -> bucketing events over time (pandas resample/Grouper)
- z-score anomaly detection -> standardizing a series and thresholding outliers
- log-template clustering -> TF-IDF vectorization + KMeans to collapse many noisy
  log lines into a few representative templates (a staple of log analytics)
- top-entity frequency    -> value_counts
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer


def load_run(run_dir: Path) -> pd.DataFrame:
    """Load all Parquet files in a run into one DataFrame (ts parsed to datetime)."""
    con = duckdb.connect(database=":memory:")
    try:
        glob = str(run_dir / "*.parquet")
        df = con.execute(f"SELECT * FROM read_parquet('{glob}')").df()
    finally:
        con.close()
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def severity_over_time(df: pd.DataFrame, freq: str = "1h") -> pd.DataFrame:
    """Counts per severity in each time bucket (a pivot of resampled counts)."""
    if df.empty:
        return pd.DataFrame()
    grouped = (
        df.set_index("ts")
        .groupby([pd.Grouper(freq=freq), "severity"])
        .size()
        .unstack(fill_value=0)
    )
    return grouped


def detect_anomalies(df: pd.DataFrame, freq: str = "1h", z: float = 3.0) -> pd.DataFrame:
    """Flag time buckets whose event count is a statistical outlier.

    Standardize the per-bucket count series to z-scores ((x - mean) / std) and keep
    buckets at or above the threshold. Returns columns: n, zscore (empty if the
    series has no variance — nothing stands out).
    """
    if df.empty:
        return pd.DataFrame(columns=["n", "zscore"])
    counts = df.set_index("ts").resample(freq).size().rename("n")
    sigma = float(counts.std(ddof=0))
    if sigma == 0:
        return pd.DataFrame(columns=["n", "zscore"])
    zscores = (counts - counts.mean()) / sigma
    out = pd.DataFrame({"n": counts, "zscore": zscores})
    return out[out["zscore"] >= z].sort_values("zscore", ascending=False)


def top_entities(df: pd.DataFrame, n: int = 10) -> pd.Series:
    """Which entities (pods/services/IPs) produced the most signals."""
    if df.empty:
        return pd.Series(dtype=int)
    return df["entity"].value_counts().head(n)


def cluster_messages(messages: list[str], k: int = 5) -> pd.DataFrame:
    """Group similar log messages into templates via TF-IDF + KMeans.

    TF-IDF turns each message into a weighted term vector; KMeans groups nearby
    vectors. The most frequent raw message in each cluster is its template.
    Returns columns: cluster, size, template (largest cluster first).
    """
    msgs = [m for m in messages if m]
    if not msgs:
        return pd.DataFrame(columns=["cluster", "size", "template"])

    n_clusters = max(1, min(k, len(set(msgs))))
    vectors = TfidfVectorizer(stop_words="english").fit_transform(msgs)
    labels = KMeans(n_clusters=n_clusters, n_init=10, random_state=0).fit_predict(vectors)

    frame = pd.DataFrame({"message": msgs, "cluster": labels})
    rows = []
    for cid, grp in frame.groupby("cluster"):
        template = grp["message"].value_counts().idxmax()
        rows.append({"cluster": int(cid), "size": len(grp), "template": template})
    return pd.DataFrame(rows).sort_values("size", ascending=False).reset_index(drop=True)


def summary(run_dir: Path, freq: str = "1h", k: int = 5) -> dict:
    """One-call bundle used by the CLI and the agent tool."""
    df = load_run(run_dir)
    return {
        "rows": len(df),
        "severity_over_time": severity_over_time(df, freq),
        "anomalies": detect_anomalies(df, freq),
        "top_entities": top_entities(df),
        "clusters": cluster_messages(df["message"].tolist() if not df.empty else [], k),
    }


def _fmt(obj: object) -> str:
    if isinstance(obj, pd.DataFrame):
        return "(none)" if obj.empty else obj.to_string()
    if isinstance(obj, pd.Series):
        return "(none)" if obj.empty else obj.to_string()
    return str(obj)


def render_summary(s: dict) -> str:
    """Human-readable text version of `summary()`."""
    return (
        f"rows: {s['rows']}\n\n"
        f"== severity over time ==\n{_fmt(s['severity_over_time'])}\n\n"
        f"== anomalous buckets (z>=3) ==\n{_fmt(s['anomalies'])}\n\n"
        f"== top entities ==\n{_fmt(s['top_entities'])}\n\n"
        f"== message clusters ==\n{_fmt(s['clusters'])}\n"
    )
