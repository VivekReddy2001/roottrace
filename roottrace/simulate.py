"""Telemetry simulation with fault injection.

For every node and minute we produce three metrics:

* ``latency`` - response time observed by callers (services) or 0 (hosts);
* ``errors``  - error rate in [0, 1] (services) or 0 (hosts);
* ``cpu``     - utilisation in [0, 1].

Propagation follows the dependency graph in reverse topological order: a
service's latency is its own processing time plus a fraction of each
dependency's latency, and errors propagate upstream with a per-edge
probability (retries hide some). Host CPU saturation slows every service on
the host.

Faults are injected at one root node from ``fault_start`` to the end of the
window. To keep the problem honest, *distractors* - short, smaller anomalies
on unrelated nodes - are injected during the same window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from .topology import hosts, services

METRICS = ("latency", "errors", "cpu")
FAULT_TYPES = ("latency", "errors", "cpu", "subtle")


@dataclass
class Incident:
    graph: nx.DiGraph
    root: str
    fault: str
    metrics: dict[str, np.ndarray]  # metric -> (n_nodes, T)
    nodes: list[str]
    fault_start: int
    distractors: list[str] = field(default_factory=list)

    def series(self, node: str, metric: str) -> np.ndarray:
        return self.metrics[metric][self.nodes.index(node)]


def _pick_root(g: nx.DiGraph, fault: str, rng: np.random.Generator) -> str:
    if fault == "cpu":
        candidates = hosts(g)
    else:
        candidates = [n for n in services(g) if g.nodes[n]["kind"] != "frontend"]
    return str(rng.choice(candidates))


def simulate(
    g: nx.DiGraph,
    fault: str,
    seed: int = 0,
    root: str | None = None,
    T: int = 120,
    fault_start: int = 90,
    noise: float = 0.08,
    n_distractors: int = 2,
    unobserved: float = 0.3,
) -> Incident:
    if fault not in FAULT_TYPES:
        raise ValueError(f"unknown fault type: {fault}")
    rng = np.random.default_rng(seed)
    nodes = list(g.nodes)
    idx = {n: i for i, n in enumerate(nodes)}
    N = len(nodes)
    root = root or _pick_root(g, fault, rng)

    # static per-node / per-edge parameters
    base_lat = rng.lognormal(np.log(8), 0.5, N)
    base_err = rng.uniform(0.001, 0.01, N)
    base_cpu = rng.uniform(0.15, 0.45, N)
    # calls per request: fan-out and retries mean a caller can wait on a
    # dependency more than once, which *amplifies* a fault on the way up
    call_w = {e: rng.uniform(0.5, 2.0) for e in g.edges}
    err_p = {e: rng.uniform(0.3, 0.9) for e in g.edges}
    # nodes differ in how noisy they are; datastores are the noisiest
    node_noise = noise * rng.lognormal(0, 0.5, N)
    for n, i in idx.items():
        if g.nodes[n]["kind"] in ("database", "cache", "queue"):
            node_noise[i] *= 1.5
    nn = node_noise[:, None]

    # own (pre-propagation) behaviour over time, with multiplicative noise
    own_lat = base_lat[:, None] * np.exp(rng.normal(0, 1, (N, T)) * nn)
    own_err = np.clip(base_err[:, None] * np.exp(rng.normal(0, 1, (N, T)) * 2 * nn), 0, 1)
    cpu = np.clip(base_cpu[:, None] + rng.normal(0, 1, (N, T)) * nn / 3, 0, 1)

    # daily-pattern-free load wobble shared by the whole system
    wobble = 1 + 0.05 * np.sin(np.linspace(0, 4 * np.pi, T))
    own_lat *= wobble

    # noise-free end-to-end latency of every node, so that fault sizes can be
    # stated relative to what the node's callers normally observe
    order = list(reversed(list(nx.topological_sort(g))))
    base_total = base_lat.copy()
    for n in order:
        for v in g.successors(n):
            if g.edges[n, v]["rel"] == "CALLS":
                base_total[idx[n]] += call_w[(n, v)] * base_total[idx[v]]

    win = slice(fault_start, T)
    r = idx[root]
    if fault == "latency":  # e.g. lock contention: +100..300% of normal latency
        own_lat[r, win] += rng.uniform(1.0, 3.0) * base_total[r]
        cpu[r, win] = np.clip(cpu[r, win] + rng.uniform(0.1, 0.3), 0, 1)
    elif fault == "errors":  # e.g. a bad deploy
        own_err[r, win] = np.clip(own_err[r, win] + rng.uniform(0.2, 0.5), 0, 1)
    elif fault == "cpu":  # noisy neighbour / runaway process on a host
        cpu[r, win] = np.clip(cpu[r, win] + rng.uniform(0.45, 0.6), 0, 1)
    elif fault == "subtle":  # a slow query: +10..30% latency, nothing else
        own_lat[r, win] += rng.uniform(0.1, 0.3) * base_total[r]

    # distractors: short, weaker anomalies elsewhere
    others = [n for n in services(g) if n != root and not nx.has_path(g, n, root) and not nx.has_path(g, root, n)]
    if len(others) < n_distractors:
        others = [n for n in services(g) if n != root]
    distractors = [str(x) for x in rng.choice(others, size=min(n_distractors, len(others)), replace=False)]
    for d in distractors:
        # unrelated slowdowns of random onset and length; the longer ones are
        # indistinguishable from a real fault by their own metrics alone
        s = int(rng.integers(fault_start - 5, T - 10))
        e = int(rng.integers(s + 10, T + 1))
        own_lat[idx[d], s:e] *= rng.uniform(1.2, 1.8)

    # host CPU = own background + load from its services; saturation slows services
    host_of = {u: v for u, v, rel in g.edges(data="rel") if rel == "RUNS_ON"}
    for h in hosts(g):
        tenants = [idx[u] for u, v in host_of.items() if v == h]
        if tenants:
            cpu[idx[h]] = np.clip(cpu[idx[h]] + 0.3 * (cpu[tenants].mean(axis=0) - 0.3), 0, 1)
    for u, h in host_of.items():
        sat = np.clip((cpu[idx[h]] - 0.75) / 0.25, 0, None)
        own_lat[idx[u]] *= 1 + 3 * sat

    # propagate latency and errors from dependencies to dependants
    latency = np.zeros((N, T))
    errors = np.zeros((N, T))
    for n in order:
        i = idx[n]
        if g.nodes[n]["kind"] == "host":
            continue
        lat = own_lat[i].copy()
        ok = 1 - own_err[i]
        for v in g.successors(n):
            if g.edges[n, v]["rel"] != "CALLS":
                continue
            lat += call_w[(n, v)] * latency[idx[v]]
            ok *= (1 - err_p[(n, v)] * errors[idx[v]]) ** call_w[(n, v)]
        latency[i] = lat
        errors[i] = 1 - ok

    # observability gaps: some datastores export only CPU (managed services
    # rarely expose server-side latency or error rate)
    observed = {"latency": np.ones(N, bool), "errors": np.ones(N, bool)}
    for n, i in idx.items():
        kind = g.nodes[n]["kind"]
        if kind == "host" or (kind in ("database", "cache", "queue") and rng.random() < unobserved):
            observed["latency"][i] = observed["errors"][i] = False
    latency[~observed["latency"]] = 0.0
    errors[~observed["errors"]] = 0.0

    return Incident(g, root, fault, {"latency": latency, "errors": errors, "cpu": cpu}, nodes, fault_start, distractors)
