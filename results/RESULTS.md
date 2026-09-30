### Main benchmark (600 held-out incidents)

| Ranker | AC@1 | AC@3 | AC@5 | Avg@5 | MRR |
|---|---:|---:|---:|---:|---:|
| **roottrace** | **0.913** | **0.978** | **0.983** | **0.965** | **0.946** |
| frontier | 0.913 | 0.960 | 0.965 | 0.950 | 0.938 |
| anomaly | 0.857 | 0.945 | 0.955 | 0.926 | 0.902 |
| pagerank | 0.807 | 0.902 | 0.953 | 0.893 | 0.866 |
| random_walk | 0.470 | 0.723 | 0.842 | 0.695 | 0.624 |
| random | 0.027 | 0.077 | 0.112 | 0.073 | 0.097 |

### Top-1 accuracy by fault type

| Fault | anomaly | frontier | pagerank | random_walk | roottrace |
|---|---:|---:|---:|---:|---:|
| latency | 0.93 | 0.99 | 0.97 | 0.55 | 0.97 |
| errors | 0.89 | 0.93 | 0.95 | 0.55 | 0.95 |
| cpu | 0.86 | 0.99 | 0.99 | 0.57 | 0.99 |
| subtle | 0.74 | 0.74 | 0.32 | 0.22 | 0.75 |

### Sweep: Metric noise (log-scale sigma) (180 incidents per point, AC@1)

| Ranker | 0.05 | 0.1 | 0.2 | 0.35 | 0.5 |
|---|---:|---:|---:|---:|---:|
| random | 0.02 | 0.02 | 0.02 | 0.02 | 0.02 |
| anomaly | 0.87 | 0.86 | 0.78 | 0.72 | 0.62 |
| frontier | 0.92 | 0.91 | 0.84 | 0.74 | 0.63 |
| pagerank | 0.84 | 0.79 | 0.72 | 0.63 | 0.50 |
| random_walk | 0.44 | 0.46 | 0.38 | 0.31 | 0.22 |
| roottrace | 0.93 | 0.89 | 0.84 | 0.74 | 0.61 |

### Sweep: Share of datastores exporting only CPU (180 incidents per point, AC@1)

| Ranker | 0.0 | 0.3 | 0.6 | 0.9 |
|---|---:|---:|---:|---:|
| random | 0.02 | 0.02 | 0.02 | 0.02 |
| anomaly | 0.89 | 0.86 | 0.84 | 0.83 |
| frontier | 0.94 | 0.91 | 0.88 | 0.88 |
| pagerank | 0.82 | 0.81 | 0.78 | 0.78 |
| random_walk | 0.44 | 0.46 | 0.48 | 0.49 |
| roottrace | 0.95 | 0.91 | 0.89 | 0.88 |

### Sweep: Concurrent unrelated anomalies (180 incidents per point, AC@1)

| Ranker | 0 | 2 | 5 | 10 |
|---|---:|---:|---:|---:|
| random | 0.02 | 0.02 | 0.02 | 0.02 |
| anomaly | 0.90 | 0.86 | 0.82 | 0.78 |
| frontier | 0.95 | 0.91 | 0.87 | 0.83 |
| pagerank | 0.82 | 0.81 | 0.77 | 0.74 |
| random_walk | 0.51 | 0.46 | 0.36 | 0.29 |
| roottrace | 0.95 | 0.91 | 0.87 | 0.82 |
