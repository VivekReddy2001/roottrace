"""Neo4j backend: store topologies and incident evidence as a property graph,
and answer RCA questions in Cypher.

Model::

    (:Component {name, kind, layer})-[:CALLS]->(:Component)
    (:Component)-[:RUNS_ON]->(:Component:Host)
    (:Incident {id, fault, root})-[:SCORED {anomaly, observed}]->(:Component)

Keeping per-incident scores on a relationship (not a node property) lets
many incidents share one topology and be queried side by side.
"""

from __future__ import annotations

import os

import networkx as nx
from neo4j import GraphDatabase

from .detect import observed
from .simulate import Incident

SCHEMA = [
    "CREATE CONSTRAINT component_name IF NOT EXISTS FOR (c:Component) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT incident_id IF NOT EXISTS FOR (i:Incident) REQUIRE i.id IS UNIQUE",
]

# Anomalous components none of whose dependencies are anomalous: the place
# where the anomalous region of the dependency graph ends.
FRONTIER = """
MATCH (i:Incident {id: $incident})-[s:SCORED]->(c:Component)
WHERE s.anomaly >= $threshold
  AND NOT EXISTS {
    MATCH (c)-[:CALLS|RUNS_ON]->(d:Component)<-[t:SCORED]-(i)
    WHERE t.anomaly >= $threshold
  }
RETURN c.name AS name, c.kind AS kind, s.anomaly AS anomaly
ORDER BY anomaly DESC, name
"""

# The shortest chain from an entry point to a candidate root whose
# intermediate hops are all anomalous: the "how did a slow database reach
# the user" explanation.
PROPAGATION_PATH = """
MATCH (i:Incident {id: $incident})
MATCH (f:Component {kind: 'frontend'}), (r:Component {name: $root})
MATCH p = shortestPath((f)-[:CALLS|RUNS_ON*..12]->(r))
WHERE all(n IN nodes(p)[1..-1] WHERE EXISTS {
    MATCH (i)-[t:SCORED]->(n) WHERE t.anomaly >= $threshold })
RETURN [n IN nodes(p) | n.name] AS path
ORDER BY length(p)
LIMIT 1
"""

# Blast radius: every component that (transitively) depends on the root.
BLAST_RADIUS = """
MATCH (r:Component {name: $root})<-[:CALLS|RUNS_ON*1..]-(u:Component)
RETURN DISTINCT u.name AS name ORDER BY name
"""


class Neo4jStore:
    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None):
        uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = user or os.environ.get("NEO4J_USER", "neo4j")
        password = password or os.environ.get("NEO4J_PASSWORD", "roottrace-dev")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _run(self, query: str, **params) -> list[dict]:
        with self.driver.session() as s:
            return [r.data() for r in s.run(query, **params)]

    # ------------------------------------------------------------ writes
    def init_schema(self) -> None:
        for q in SCHEMA:
            self._run(q)

    def clear(self) -> None:
        self._run("MATCH (n) DETACH DELETE n")

    def load_topology(self, g: nx.DiGraph) -> None:
        nodes = [{"name": n, "kind": d["kind"], "layer": d["layer"]} for n, d in g.nodes(data=True)]
        self._run(
            """
            UNWIND $nodes AS n
            MERGE (c:Component {name: n.name})
            SET c.kind = n.kind, c.layer = n.layer
            FOREACH (_ IN CASE WHEN n.kind = 'host' THEN [1] ELSE [] END | SET c:Host)
        """,
            nodes=nodes,
        )
        for rel in ("CALLS", "RUNS_ON"):
            edges = [{"u": u, "v": v} for u, v, r in g.edges(data="rel") if r == rel]
            self._run(
                f"""
                UNWIND $edges AS e
                MATCH (a:Component {{name: e.u}}), (b:Component {{name: e.v}})
                MERGE (a)-[:{rel}]->(b)
            """,
                edges=edges,
            )

    def load_incident(self, incident_id: str, inc: Incident, scores: dict[str, float]) -> None:
        seen = observed(inc)
        self._run(
            "MERGE (i:Incident {id: $id}) SET i.fault = $fault, i.root = $root",
            id=incident_id,
            fault=inc.fault,
            root=inc.root,
        )
        rows = [{"name": n, "anomaly": float(s), "observed": seen[n]} for n, s in scores.items()]
        self._run(
            """
            MATCH (i:Incident {id: $id})
            UNWIND $rows AS r
            MATCH (c:Component {name: r.name})
            MERGE (i)-[s:SCORED]->(c)
            SET s.anomaly = r.anomaly, s.observed = r.observed
        """,
            id=incident_id,
            rows=rows,
        )

    # ------------------------------------------------------------- reads
    def frontier(self, incident_id: str, threshold: float = 3.0) -> list[dict]:
        return self._run(FRONTIER, incident=incident_id, threshold=threshold)

    def propagation_path(self, incident_id: str, root: str, threshold: float = 3.0) -> list[str]:
        rows = self._run(PROPAGATION_PATH, incident=incident_id, root=root, threshold=threshold)
        return rows[0]["path"] if rows else []

    def blast_radius(self, root: str) -> list[str]:
        return [r["name"] for r in self._run(BLAST_RADIUS, root=root)]

    def fetch_graph(self) -> nx.DiGraph:
        """Read the topology back, so any ranker can run on Neo4j-held data."""
        g = nx.DiGraph()
        for r in self._run("MATCH (c:Component) RETURN c.name AS name, c.kind AS kind, c.layer AS layer"):
            g.add_node(r["name"], kind=r["kind"], layer=r["layer"])
        for r in self._run(
            "MATCH (a:Component)-[e:CALLS|RUNS_ON]->(b:Component) RETURN a.name AS u, b.name AS v, type(e) AS rel"
        ):
            g.add_edge(r["u"], r["v"], rel=r["rel"])
        return g

    def fetch_scores(self, incident_id: str) -> dict[str, float]:
        rows = self._run(
            "MATCH (:Incident {id: $id})-[s:SCORED]->(c) RETURN c.name AS name, s.anomaly AS a", id=incident_id
        )
        return {r["name"]: r["a"] for r in rows}
