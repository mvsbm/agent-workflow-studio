"""How much does a result move from active-learning luck alone? Same config, different seeds."""

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

root, out = Path(sys.argv[1]), Path(sys.argv[2])
fixes = tuple(sys.argv[3].split(",")) if len(sys.argv) > 3 and sys.argv[3] else ()
report = {}
for name in ("base7", "wild7", "dirty7", "hard7", "hard11"):
    recs = P.load(root / f"data_{name}", "full", fixes)
    key = P.load_key(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
         "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    d["pairs"] = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs))
    X = B.features(recs, d["pairs"], P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
    f1s = []
    for seed in range(5):
        prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr", seed=seed)
        f1s.append(finish(d, prob)["f1"])
    report[name] = {"f1": f1s, "mean": round(float(np.mean(f1s)), 4), "std": round(float(np.std(f1s)), 4)}
    print(f"{name:8s} F1 over 5 seeds: {[round(f, 4) for f in f1s]}  mean {np.mean(f1s):.4f}  std {np.std(f1s):.4f}", flush=True)
out.write_text(json.dumps(report, indent=1))
