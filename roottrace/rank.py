"""Root-cause rankers. Each takes an incident and its anomaly scores and
returns every node, most likely root cause first.

* ``random``       - uniform shuffle; the floor every method must beat.
* ``anomaly``      - rank by anomaly score alone (no graph).
* ``frontier``     - anomalous nodes none of whose dependencies are
                     anomalous, i.e. where the anomalous region "ends".
* ``pagerank``     - personalised PageRank along dependency edges, restart
                     distribution proportional to anomaly score.
* ``random_walk``  - correlation-weighted random walk with backward and self
                     transitions (after CloudRanger / MicroCause), started
                     from the entry points.
* ``roottrace``    - this project's ranker: a correlation-weighted walk with
                     anomaly-proportional restarts, followed by an
                     "explained-away" discount for nodes whose anomaly is
                     accounted for by an anomalous dependency.
"""

from __future__ import annotations

from collections.abc import Callable

import networkx as nx
import numpy as np

from .detect import is_anomalous, observed
from .simulate import METRICS, Incident
from .topology import entrypoints

Ranker = Callable[[Incident, dict[str, float]], list[str]]


def _order(scores: dict[str, float]) -> list[str]:
    return [n for n, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def rank_random(inc: Incident, scores: dict[str, float], seed: int = 0) -> list[str]:
    nodes = sorted(inc.nodes)
    np.random.default_rng(seed).shuffle(nodes)
    return nodes


def rank_anomaly(inc: Incident, scores: dict[str, float]) -> list[str]:
    return _order(scores)


def rank_frontier(inc: Incident, scores: dict[str, float], threshold: float = 3.0) -> list[str]:
    bad = is_anomalous(scores, threshold)
    g = inc.graph
    frontier = {n: s for n, s in scores.items() if n in bad and not any(v in bad for v in g.successors(n))}
    rest = {n: s for n, s in scores.items() if n not in frontier}
    return _order(frontier) + _order(rest)


def rank_pagerank(inc: Incident, scores: dict[str, float], alpha: float = 0.85) -> list[str]:
    total = sum(scores.values()) or 1.0
    personal = {n: s / total for n, s in scores.items()} if total > 0 else None
    g = inc.graph.copy()
    for u, v in g.edges:
        g.edges[u, v]["w"] = scores[v] + 1e-6  # move toward anomalous dependencies
    pr = nx.pagerank(g, alpha=alpha, personalization=personal, weight="w", max_iter=500, tol=1e-10)
    return _order(pr)


# ---------------------------------------------------------------- correlation walk
def _correlations(inc: Incident, window_start: int) -> np.ndarray:
    """|Pearson| between every pair of nodes, maximised over metric pairs,
    on the minutes around and after the fault onset."""
    feats = []
    for m in METRICS:
        x = inc.metrics[m][:, window_start:]
        x = x - x.mean(axis=1, keepdims=True)
        sd = x.std(axis=1, keepdims=True)
        feats.append(np.where(sd > 1e-12, x / np.maximum(sd, 1e-12), 0.0))
    n_t = feats[0].shape[1]
    best = np.zeros((len(inc.nodes), len(inc.nodes)))
    for a in feats:
        for b in feats:
            best = np.maximum(best, np.abs(a @ b.T) / n_t)
    return best


def _walk(
    inc: Incident, scores, restart: np.ndarray, corr: np.ndarray, back: float, restart_p: float, steps: int = 200
) -> np.ndarray:
    idx = {n: i for i, n in enumerate(inc.nodes)}
    N = len(inc.nodes)
    norm = {n: s / (1.0 + s) for n, s in scores.items()}
    P = np.zeros((N, N))
    for u in inc.nodes:
        i = idx[u]
        fwd = [idx[v] for v in inc.graph.successors(u)]
        bwd = [idx[v] for v in inc.graph.predecessors(u)]
        for j in fwd:
            P[i, j] += corr[i, j] * norm[inc.nodes[j]]
        for j in bwd:
            P[i, j] += back * corr[i, j] * norm[inc.nodes[j]]
        # stay when this node is more anomalous than every dependency: the
        # walk has reached the place where the anomaly stops propagating
        here = norm[u]
        deeper = max((norm[v] for v in inc.graph.successors(u)), default=0.0)
        P[i, i] += max(0.0, here - deeper)
        if P[i].sum() == 0:
            P[i, i] = 1.0
        P[i] /= P[i].sum()
    x = restart.copy()
    for _ in range(steps):
        x = (1 - restart_p) * (x @ P) + restart_p * restart
    return x


def rank_random_walk(inc: Incident, scores: dict[str, float], back: float = 0.3) -> list[str]:
    corr = _correlations(inc, max(0, inc.fault_start - 30))
    idx = {n: i for i, n in enumerate(inc.nodes)}
    restart = np.zeros(len(inc.nodes))
    for e in entrypoints(inc.graph):
        restart[idx[e]] = 1.0
    restart /= restart.sum()
    x = _walk(inc, scores, restart, corr, back, restart_p=0.15)
    return _order({n: float(x[idx[n]]) for n in inc.nodes})


def fill_blind_spots(inc: Incident, scores: dict[str, float], threshold: float = 3.0) -> dict[str, float]:
    """Give nodes that export no request metrics a score inferred from
    their callers.

    A datastore that only reports CPU cannot show its own latency or error
    rate, but its callers can. When anomalous callers have no *other*
    anomalous dependency to blame, the unobserved dependency inherits the
    evidence, scaled by the fraction of its callers that are anomalous.
    """
    seen = observed(inc)
    bad = is_anomalous(scores, threshold)
    out = dict(scores)
    for v in inc.nodes:
        if seen[v] or inc.graph.nodes[v]["kind"] == "host":
            continue
        callers = [u for u in inc.graph.predecessors(v) if inc.graph.edges[u, v]["rel"] == "CALLS"]
        if not callers:
            continue
        unexplained = [u for u in callers if u in bad and not any(w in bad for w in inc.graph.successors(u) if w != v)]
        if unexplained:
            share = len(unexplained) / len(callers)
            out[v] = max(out[v], share * max(scores[u] for u in unexplained))
    return out


def rank_roottrace(
    inc: Incident, scores: dict[str, float], back: float = 0.2, restart_p: float = 0.3, threshold: float = 3.0
) -> list[str]:
    scores = fill_blind_spots(inc, scores, threshold)
    corr = _correlations(inc, max(0, inc.fault_start - 30))
    idx = {n: i for i, n in enumerate(inc.nodes)}
    a = np.array([scores[n] for n in inc.nodes])
    restart = a / a.sum() if a.sum() > 0 else np.full(len(a), 1 / len(a))
    x = _walk(inc, scores, restart, corr, back, restart_p)

    # explained-away: if an anomalous dependency is at least as anomalous
    # and strongly correlated, this node is more likely a victim than a cause
    out = {}
    for n in inc.nodes:
        i = idx[n]
        explain = 0.0
        if a[i] >= threshold:
            for v in inc.graph.successors(n):
                j = idx[v]
                if a[j] >= threshold:
                    explain = max(explain, corr[i, j] * min(1.0, a[j] / a[i]))
        out[n] = float(x[i] * (1 - 0.9 * explain) * np.log1p(a[i]))
    return _order(out)


RANKERS: dict[str, Ranker] = {
    "random": rank_random,
    "anomaly": rank_anomaly,
    "frontier": rank_frontier,
    "pagerank": rank_pagerank,
    "random_walk": rank_random_walk,
    "roottrace": rank_roottrace,
}
