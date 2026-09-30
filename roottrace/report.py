"""Run the full benchmark on held-out seeds and write tables and figures."""

from __future__ import annotations

import json
from pathlib import Path

from .evaluate import cases, run, save, table
from .rank import RANKERS
from .simulate import FAULT_TYPES

# fixed categorical order (colour follows the ranker, never its rank)
COLORS = {
    "roottrace": "#2a78d6",
    "frontier": "#eb6834",
    "anomaly": "#1baf7a",
    "pagerank": "#eda100",
    "random_walk": "#e87ba4",
    "random": "#8a8983",
}
SWEEPS = {
    "noise": ("Metric noise (log-scale sigma)", [0.05, 0.1, 0.2, 0.35, 0.5]),
    "unobserved": ("Share of datastores exporting only CPU", [0.0, 0.3, 0.6, 0.9]),
    "n_distractors": ("Concurrent unrelated anomalies", [0, 2, 5, 10]),
}


def _md_table(summary: dict, keys=("AC@1", "AC@3", "AC@5", "Avg@5", "MRR")) -> str:
    rows = sorted(summary.items(), key=lambda kv: -kv[1]["Avg@5"])
    out = ["| Ranker | " + " | ".join(keys) + " |", "|---|" + "---:|" * len(keys)]
    for k, v in rows:
        name = k[-1]
        cells = [f"{v[m]:.3f}" for m in keys]
        if name == "roottrace":
            name, cells = f"**{name}**", [f"**{c}**" for c in cells]
        out.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def _fault_table(by_fault: dict) -> str:
    names = [n for n in RANKERS if n != "random"]
    out = ["| Fault | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    for f in FAULT_TYPES:
        out.append(f"| {f} | " + " | ".join(f"{by_fault[(f, n)]['AC@1']:.2f}" for n in names) + " |")
    return "\n".join(out)


def _plot_sweep(results: dict, name: str, label: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.6, 3.9), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    xs = [x for x, _ in results]
    for ranker in RANKERS:
        ys = [summ[(ranker,)]["AC@1"] for _, summ in results]
        lw = 2.4 if ranker == "roottrace" else 1.6
        ax.plot(
            range(len(xs)),
            ys,
            color=COLORS[ranker],
            lw=lw,
            marker="o",
            ms=4.5,
            label=ranker,
            zorder=3 if ranker == "roottrace" else 2,
        )
    ax.set_xticks(range(len(xs)), [str(x) for x in xs])
    ax.set_xlabel(label, color="#52514e")
    ax.set_ylabel("Top-1 accuracy (AC@1)", color="#52514e")
    ax.set_ylim(0, 1.02)
    ax.grid(axis="y", color="#e4e3dd", lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#b5b4ad")
    ax.tick_params(colors="#52514e")
    ax.legend(frameon=False, fontsize=8, ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.2))
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def run_benchmark(out: Path, topologies: int = 5, incidents: int = 10) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    main_cases = cases(topologies=topologies, incidents=incidents, split="test")
    rows = run(main_cases)
    save(rows, out / "main.jsonl")
    summary = {
        "n_incidents": len(main_cases),
        "main": {k[-1]: v for k, v in table(rows).items()},
        "by_fault": {f"{k[0]}/{k[1]}": v for k, v in table(rows, ("fault",)).items()},
        "by_size": {f"{k[0]}/{k[1]}": v for k, v in table(rows, ("size",)).items()},
        "ms_per_incident": {r: sum(x["ms"] for x in rows if x["ranker"] == r) / len(main_cases) for r in RANKERS},
        "sweeps": {},
    }

    md = [
        f"### Main benchmark ({len(main_cases)} held-out incidents)\n",
        _md_table(table(rows)),
        "\n### Top-1 accuracy by fault type\n",
        _fault_table(table(rows, ("fault",))),
    ]

    sweep_cases = cases(topologies=3, incidents=5, split="test")
    for name, (label, values) in SWEEPS.items():
        results = []
        for v in values:
            summ = table(run(sweep_cases, log=None, **{name: v}))
            results.append((v, summ))
            summary["sweeps"].setdefault(name, {})[str(v)] = {k[-1]: s for k, s in summ.items()}
        _plot_sweep(results, name, label, out / f"sweep_{name}.png")
        md.append(f"\n### Sweep: {label} ({len(sweep_cases)} incidents per point, AC@1)\n")
        head = "| Ranker | " + " | ".join(str(v) for v in values) + " |"
        md += [head, "|---|" + "---:|" * len(values)]
        for r in RANKERS:
            md.append(f"| {r} | " + " | ".join(f"{s[(r,)]['AC@1']:.2f}" for _, s in results) + " |")

    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "RESULTS.md").write_text("\n".join(md) + "\n")
    return summary
