"""Unit tests for topology, simulation, detection, rankers and metrics."""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from roottrace.detect import anomaly_scores, observed, robust_z
from roottrace.evaluate import cases, rank_of, run, summarise, table
from roottrace.rank import RANKERS, fill_blind_spots
from roottrace.simulate import FAULT_TYPES, simulate
from roottrace.topology import entrypoints, generate_topology, hosts, services


@pytest.fixture(scope="module")
def topo():
    return generate_topology(40, seed=3)


def chain() -> nx.DiGraph:
    """frontend -> gateway -> a -> b -> db, everything on one host."""
    g = nx.DiGraph()
    for i, (n, k) in enumerate(
        [
            ("frontend-0", "frontend"),
            ("gateway-0", "gateway"),
            ("svc-a", "service"),
            ("svc-b", "service"),
            ("db", "database"),
            ("host-0", "host"),
        ]
    ):
        g.add_node(n, kind=k, layer=i)
    for u, v in [("frontend-0", "gateway-0"), ("gateway-0", "svc-a"), ("svc-a", "svc-b"), ("svc-b", "db")]:
        g.add_edge(u, v, rel="CALLS")
    for n in ["gateway-0", "svc-a", "svc-b", "db"]:
        g.add_edge(n, "host-0", rel="RUNS_ON")
    return g


# ------------------------------------------------------------------ topology
@pytest.mark.parametrize("n", [6, 20, 50, 120])
def test_topology_is_a_connected_dag(n):
    g = generate_topology(n, seed=n)
    assert nx.is_directed_acyclic_graph(g)
    assert len(services(g)) == n
    # every service is reachable from some entry point
    reach = set().union(*(nx.descendants(g, e) | {e} for e in entrypoints(g)))
    assert set(services(g)) <= reach
    # every non-host runs on exactly one host
    for s in services(g):
        if g.nodes[s]["kind"] != "frontend":
            assert sum(g.edges[s, v]["rel"] == "RUNS_ON" for v in g.successors(s)) == 1


def test_topology_is_deterministic():
    a, b = generate_topology(30, seed=9), generate_topology(30, seed=9)
    assert list(a.edges(data=True)) == list(b.edges(data=True))


def test_shared_infrastructure_has_fan_in(topo):
    stores = [n for n, d in topo.nodes(data=True) if d["kind"] in ("database", "cache", "queue")]
    assert max(topo.in_degree(s) for s in stores) >= 3


# ---------------------------------------------------------------- simulation
def test_fault_propagates_upstream_only():
    g = chain()
    inc = simulate(g, "latency", root="svc-b", seed=0, n_distractors=0, unobserved=0.0)
    s = anomaly_scores(inc)
    assert s["svc-b"] > 3 and s["svc-a"] > 3 and s["gateway-0"] > 3  # root and its callers
    assert s["db"] < 3  # the root's own dependency is healthy


# "subtle" is excluded on purpose: at the low end of its range (+10%) it can
# sit inside normal variation, which is what makes it the hard case.
@pytest.mark.parametrize("fault", [f for f in FAULT_TYPES if f != "subtle"])
def test_every_fault_type_is_detectable_at_low_noise(topo, fault):
    inc = simulate(topo, fault, seed=1, noise=0.03, n_distractors=0, unobserved=0.0)
    s = anomaly_scores(inc)
    assert s[inc.root] >= 3


def test_cpu_faults_hit_hosts(topo):
    inc = simulate(topo, "cpu", seed=4)
    assert inc.root in hosts(topo)


def test_unobserved_nodes_export_no_request_metrics(topo):
    inc = simulate(topo, "latency", seed=2, unobserved=1.0)
    seen = observed(inc)
    for n, d in topo.nodes(data=True):
        if d["kind"] in ("database", "cache", "queue", "host"):
            assert not seen[n]
        else:
            assert seen[n]


def test_simulation_is_deterministic(topo):
    a, b = simulate(topo, "errors", seed=5), simulate(topo, "errors", seed=5)
    assert a.root == b.root
    assert all(np.array_equal(a.metrics[m], b.metrics[m]) for m in a.metrics)


# ----------------------------------------------------------------- detection
def test_robust_z_ignores_short_spikes():
    x = np.ones((2, 100))
    x += np.random.default_rng(0).normal(0, 0.01, (2, 100))
    x[0, 80:] += 1.0  # sustained shift
    x[1, 80:88] += 10.0  # short spike in a 20-minute window
    z = robust_z(x, slice(0, 70), slice(80, 100))
    assert z[0] > 10
    assert abs(z[1]) < 3


# ------------------------------------------------------------------- rankers
@pytest.mark.parametrize("name", [n for n in RANKERS if n != "random"])
def test_rankers_find_an_obvious_root(name):
    g = chain()
    inc = simulate(g, "latency", root="svc-b", seed=0, n_distractors=0, unobserved=0.0, noise=0.03)
    ranking = RANKERS[name](inc, anomaly_scores(inc))
    assert sorted(ranking) == sorted(g.nodes)
    assert ranking.index("svc-b") < 3


def test_blind_spot_inherits_evidence_from_callers():
    g = chain()
    inc = simulate(g, "errors", root="db", seed=0, n_distractors=0, unobserved=1.0, noise=0.03)
    s = anomaly_scores(inc)
    assert s["db"] < 3  # invisible on its own metrics
    filled = fill_blind_spots(inc, s)
    assert filled["db"] >= 3
    assert RANKERS["roottrace"](inc, s)[0] == "db"


# ------------------------------------------------------------------- metrics
def test_summary_metrics():
    m = summarise([1, 2, 6, 1])
    assert m["AC@1"] == 0.5 and m["AC@3"] == 0.75 and m["AC@5"] == 0.75
    assert m["MRR"] == pytest.approx((1 + 0.5 + 1 / 6 + 1) / 4)
    assert rank_of(["a", "b", "c"], "c") == 3


def test_dev_and_test_seeds_do_not_overlap():
    dev = {(c.topo_seed, c.sim_seed) for c in cases(split="dev")}
    test = {(c.topo_seed, c.sim_seed) for c in cases(split="test")}
    assert not dev & test


def test_graph_methods_beat_random():
    rows = run(cases(sizes=(20,), topologies=2, incidents=3, split="dev"), log=None)
    t = table(rows)
    assert t[("roottrace",)]["AC@3"] > t[("random",)]["AC@3"] + 0.3
