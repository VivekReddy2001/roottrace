"""Integration tests against a live Neo4j. Skipped unless NEO4J_URI is set,
e.g. ``docker compose up -d neo4j && NEO4J_URI=bolt://localhost:7687 pytest``."""

from __future__ import annotations

import os

import pytest

from roottrace.detect import anomaly_scores
from roottrace.rank import RANKERS, rank_frontier
from roottrace.simulate import simulate
from roottrace.topology import generate_topology

pytestmark = pytest.mark.skipif(not os.environ.get("NEO4J_URI"), reason="NEO4J_URI not set")


@pytest.fixture(scope="module")
def db():
    from roottrace.neo4j_store import Neo4jStore

    store = Neo4jStore()
    store.init_schema()
    store.clear()
    yield store
    store.clear()
    store.close()


@pytest.fixture(scope="module")
def loaded(db):
    g = generate_topology(40, seed=2)
    inc = simulate(g, "latency", seed=2)
    scores = anomaly_scores(inc)
    db.load_topology(g)
    db.load_incident("t1", inc, scores)
    return g, inc, scores


def test_round_trip_topology(db, loaded):
    g, _, _ = loaded
    back = db.fetch_graph()
    assert set(back.nodes) == set(g.nodes)
    assert {(u, v, d["rel"]) for u, v, d in back.edges(data=True)} == {
        (u, v, d["rel"]) for u, v, d in g.edges(data=True)
    }


def test_loading_twice_is_idempotent(db, loaded):
    g, inc, scores = loaded
    before = db._run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    db.load_topology(g)
    db.load_incident("t1", inc, scores)
    assert db._run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"] == before


def test_cypher_frontier_matches_python(db, loaded):
    _, inc, scores = loaded
    cypher = [r["name"] for r in db.frontier("t1")]
    python = rank_frontier(inc, scores)[: len(cypher)]
    assert cypher == python
    assert cypher[0] == inc.root


def test_rankers_agree_on_neo4j_data(db, loaded):
    g, inc, scores = loaded
    from dataclasses import replace

    inc_db = replace(inc, graph=db.fetch_graph())
    for name, fn in RANKERS.items():
        assert fn(inc_db, db.fetch_scores("t1")) == fn(inc, scores), name


def test_propagation_path_and_blast_radius(db, loaded):
    g, inc, _ = loaded
    path = db.propagation_path("t1", inc.root)
    assert path[0].startswith("frontend") and path[-1] == inc.root
    assert all(g.has_edge(a, b) for a, b in zip(path, path[1:], strict=False))
    import networkx as nx

    assert set(db.blast_radius(inc.root)) == nx.ancestors(g, inc.root)
