"""Per-node anomaly scores from metric time series.

Each metric is compared between a baseline window and the incident window
with a robust z-score (median / MAD), so a single noisy minute cannot
inflate or mask an anomaly. Only increases count: lower
latency or fewer errors are not symptoms.
"""

from __future__ import annotations

import numpy as np

from .simulate import METRICS, Incident


def robust_z(series: np.ndarray, baseline: slice, window: slice) -> np.ndarray:
    """Shift of the window *median* over the baseline median, in baseline
    MADs. Using the window median (not mean) means a sustained shift counts
    and a brief spike covering less than half the window does not."""
    base = series[:, baseline]
    med = np.median(base, axis=1)
    mad = 1.4826 * np.median(np.abs(base - med[:, None]), axis=1)
    scale = np.maximum(mad, 1e-3 * np.maximum(np.abs(med), 1e-6))
    return (np.median(series[:, window], axis=1) - med) / scale


def anomaly_scores(inc: Incident, baseline_end: int | None = None) -> dict[str, float]:
    """Node -> max positive robust z-score over its metrics."""
    baseline = slice(0, baseline_end or inc.fault_start - 10)
    window = slice(inc.fault_start, None)
    z = np.stack([robust_z(inc.metrics[m], baseline, window) for m in METRICS])
    score = np.clip(z, 0, None).max(axis=0)
    return {n: float(s) for n, s in zip(inc.nodes, score, strict=True)}


def is_anomalous(scores: dict[str, float], threshold: float = 3.0) -> set[str]:
    return {n for n, s in scores.items() if s >= threshold}


def observed(inc: Incident) -> dict[str, bool]:
    """Whether a node exports request metrics (latency / errors) at all."""
    lat = inc.metrics["latency"]
    err = inc.metrics["errors"]
    return {n: bool(lat[i].any() or err[i].any()) for i, n in enumerate(inc.nodes)}
