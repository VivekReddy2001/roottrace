"""``python -m roottrace <command>``

demo       simulate one incident and show every ranker's top 5
           (``--neo4j`` also loads it into Neo4j and queries it there)
benchmark  run the full evaluation and write results/ (tables + figures)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .detect import anomaly_scores
from .rank import RANKERS
from .simulate import FAULT_TYPES, simulate
from .topology import generate_topology


def cmd_demo(args) -> None:
    g = generate_topology(args.size, seed=args.seed)
    inc = simulate(g, args.fault, seed=args.seed)
    scores = anomaly_scores(inc)
    print(f"topology: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    print(f"injected: {inc.fault} fault at {inc.root}   (distractors: {', '.join(inc.distractors)})")
    print(f"anomalous components (z >= 3): {sum(s >= 3 for s in scores.values())}\n")
    for name, fn in RANKERS.items():
        top = fn(inc, scores)[:5]
        marks = [f"[{n}]" if n == inc.root else n for n in top]
        print(f"  {name:12s} {'  '.join(marks)}")

    if args.neo4j:
        from .neo4j_store import Neo4jStore

        with Neo4jStore() as db:
            db.init_schema()
            db.clear()
            db.load_topology(g)
            db.load_incident("demo", inc, scores)
            print("\nNeo4j")
            print("  frontier:", [r["name"] for r in db.frontier("demo")][:5])
            best = RANKERS["roottrace"](inc, scores)[0]
            print(f"  propagation path to {best}:", " -> ".join(db.propagation_path("demo", best)))
            print(f"  blast radius of {best}: {len(db.blast_radius(best))} components")


def cmd_benchmark(args) -> None:
    from .report import run_benchmark

    summary = run_benchmark(Path(args.out), topologies=args.topologies, incidents=args.incidents)
    print(json.dumps(summary["main"], indent=2))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="roottrace")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--size", type=int, default=40)
    d.add_argument("--fault", choices=FAULT_TYPES, default="latency")
    d.add_argument("--seed", type=int, default=71)
    d.add_argument("--neo4j", action="store_true", help="also load into Neo4j (NEO4J_URI etc.)")
    d.set_defaults(fn=cmd_demo)
    b = sub.add_parser("benchmark")
    b.add_argument("--out", default="results")
    b.add_argument("--topologies", type=int, default=5)
    b.add_argument("--incidents", type=int, default=10)
    b.set_defaults(fn=cmd_benchmark)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
