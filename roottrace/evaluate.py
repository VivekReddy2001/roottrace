"""Benchmark: many incidents, every ranker, top-k accuracy.

Metrics (standard in the microservice-RCA literature):

* ``AC@k``  - fraction of incidents whose true root is in the top k;
* ``Avg@5`` - mean of AC@1..AC@5;
* ``MRR``   - mean reciprocal rank of the true root.

Incidents are generated from two disjoint seed ranges. ``dev`` seeds were
used while designing the rankers; every number reported in the README comes
from ``test`` seeds that were never looked at during development.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np

from .detect import anomaly_scores
from .rank import RANKERS
from .simulate import FAULT_TYPES, simulate
from .topology import generate_topology

SEED_RANGES = {"dev": range(0, 10_000), "test": range(100_000, 110_000)}


@dataclass(frozen=True)
class Case:
    size: int
    fault: str
    topo_seed: int
    sim_seed: int


def cases(
    sizes=(20, 50, 100), faults=FAULT_TYPES, topologies: int = 5, incidents: int = 10, split: str = "test"
) -> list[Case]:
    base = SEED_RANGES[split].start
    out = []
    for size, fault, t in product(sizes, faults, range(topologies)):
        for k in range(incidents):
            out.append(Case(size, fault, base + t, base + 1000 * t + k + 97 * FAULT_TYPES.index(fault)))
    return out


def rank_of(ranking: list[str], root: str) -> int:
    return ranking.index(root) + 1


def summarise(ranks: list[int]) -> dict[str, float]:
    r = np.asarray(ranks)
    out = {f"AC@{k}": float(np.mean(r <= k)) for k in (1, 3, 5)}
    out["Avg@5"] = float(np.mean([np.mean(r <= k) for k in range(1, 6)]))
    out["MRR"] = float(np.mean(1.0 / r))
    return out


def run(case_list: list[Case], rankers=None, log=print, **sim_kwargs) -> list[dict]:
    """Simulate every case and rank it with every ranker. ``sim_kwargs`` go to
    :func:`roottrace.simulate.simulate` (noise, unobserved, n_distractors)."""
    rankers = rankers or list(RANKERS)
    topo_cache = {}
    rows = []
    t0 = time.time()
    for i, c in enumerate(case_list):
        key = (c.size, c.topo_seed)
        if key not in topo_cache:
            topo_cache[key] = generate_topology(c.size, seed=c.topo_seed)
        inc = simulate(topo_cache[key], c.fault, seed=c.sim_seed, **sim_kwargs)
        scores = anomaly_scores(inc)
        for name in rankers:
            t = time.perf_counter()
            ranking = RANKERS[name](inc, scores)
            rows.append(
                {
                    "size": c.size,
                    "fault": c.fault,
                    "topo_seed": c.topo_seed,
                    "sim_seed": c.sim_seed,
                    "ranker": name,
                    "root": inc.root,
                    "rank": rank_of(ranking, inc.root),
                    "n_nodes": len(inc.nodes),
                    "ms": 1000 * (time.perf_counter() - t),
                }
            )
        if log and (i + 1) % 100 == 0:
            log(f"{i + 1}/{len(case_list)} incidents ({time.time() - t0:.0f}s)")
    return rows


def table(rows: list[dict], by: tuple[str, ...] = ()) -> dict:
    groups: dict[tuple, list[int]] = {}
    for r in rows:
        groups.setdefault(tuple(r[b] for b in by) + (r["ranker"],), []).append(r["rank"])
    return {k: summarise(v) | {"n": len(v)} for k, v in groups.items()}


def save(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
