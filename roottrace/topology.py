"""Synthetic microservice topologies.

A topology is a ``networkx.DiGraph`` in which an edge ``u -> v`` means
"u depends on v": u calls service v, or service u runs on host v. Faults
therefore propagate *against* edge direction (a slow database makes its
callers slow), and root-cause analysis walks *along* it.

The generator produces the structural features that make RCA hard in
practice:

* layered call graphs (frontend -> gateway -> services -> datastores);
* fan-in on shared infrastructure (one database behind many services), so a
  single fault lights up large parts of the graph;
* co-location: several services on one host, so a host fault looks like
  simultaneous failures of unrelated services.
"""

from __future__ import annotations

import random

import networkx as nx

KINDS = ("frontend", "gateway", "service", "database", "cache", "queue", "host")


def generate_topology(n_services: int = 30, n_hosts: int | None = None, seed: int = 0) -> nx.DiGraph:
    """Layered DAG with ``n_services`` services plus hosts."""
    if n_services < 6:
        raise ValueError("need at least 6 services")
    rng = random.Random(seed)
    g = nx.DiGraph()

    n_front = max(1, n_services // 25)
    n_gate = max(1, n_services // 15)
    n_store = max(2, n_services // 6)
    n_mid = n_services - n_front - n_gate - n_store
    depth = max(2, min(4, n_mid // 5))
    mid_layers = [[] for _ in range(depth)]
    for i in range(n_mid):
        mid_layers[i % depth].append(f"svc-{i:03d}")

    layers = [
        [f"frontend-{i}" for i in range(n_front)],
        [f"gateway-{i}" for i in range(n_gate)],
        *mid_layers,
    ]
    stores = []
    for i in range(n_store):
        kind = rng.choices(["database", "cache", "queue"], weights=[3, 2, 1])[0]
        stores.append((f"{kind[:2]}-{i:02d}", kind))

    for li, layer in enumerate(layers):
        kind = "frontend" if li == 0 else "gateway" if li == 1 else "service"
        for name in layer:
            g.add_node(name, kind=kind, layer=li)
    for name, kind in stores:
        g.add_node(name, kind=kind, layer=len(layers))

    # calls: each node calls 1-3 nodes in the next layer, sometimes skipping one
    for li in range(len(layers) - 1):
        nxt = layers[li + 1]
        for u in layers[li]:
            for v in rng.sample(nxt, k=min(len(nxt), rng.randint(1, 3))):
                g.add_edge(u, v, rel="CALLS")
            if li + 2 < len(layers) and rng.random() < 0.25:
                g.add_edge(u, rng.choice(layers[li + 2]), rel="CALLS")
    # every downstream node needs a caller
    for li in range(1, len(layers)):
        for v in layers[li]:
            if g.in_degree(v) == 0:
                g.add_edge(rng.choice(layers[li - 1]), v, rel="CALLS")

    # datastores: popularity-skewed so a few are shared by many services
    services = [n for layer in mid_layers for n in layer]
    weights = [1.0 / (i + 1) for i in range(len(stores))]
    for u in services:
        for _ in range(rng.choice([0, 1, 1, 2])):
            v = rng.choices(stores, weights=weights)[0][0]
            g.add_edge(u, v, rel="CALLS")
    for v, _ in stores:
        if g.in_degree(v) == 0:
            g.add_edge(rng.choice(services), v, rel="CALLS")

    # hosts: every non-frontend component runs on one host
    n_hosts = n_hosts or max(3, n_services // 5)
    hosts = [f"host-{i:02d}" for i in range(n_hosts)]
    for h in hosts:
        g.add_node(h, kind="host", layer=len(layers) + 1)
    placed = [n for n in g.nodes if g.nodes[n]["kind"] not in ("host",)]
    for i, n in enumerate(placed):
        g.add_edge(n, hosts[i % n_hosts] if i < n_hosts else rng.choice(hosts), rel="RUNS_ON")

    assert nx.is_directed_acyclic_graph(g)
    return g


def services(g: nx.DiGraph) -> list[str]:
    return [n for n, d in g.nodes(data=True) if d["kind"] != "host"]


def hosts(g: nx.DiGraph) -> list[str]:
    return [n for n, d in g.nodes(data=True) if d["kind"] == "host"]


def entrypoints(g: nx.DiGraph) -> list[str]:
    return [n for n, d in g.nodes(data=True) if d["kind"] == "frontend"]
