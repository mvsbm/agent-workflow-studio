"""Remove each kept component from the final pipeline: it earned its place only if F1 drops."""

import json
import sys
from pathlib import Path

from ladder import Dataset, run

out, root, seeds = Path(sys.argv[1]), Path(sys.argv[2]), [int(s) for s in sys.argv[3:]]
best = json.loads((out / "ladder_best.json").read_text())
datasets = [Dataset(root, out, s) for s in seeds]
final = {ds.seed: run(ds, best) for ds in datasets}
variants = {
    "without standardization": {**best, "level": "raw"},
    "without set keys": {**best, "chans": [c for c in best["chans"] if c != "sets"]},
    "without sparse q-gram": {**best, "chans": [c for c in best["chans"] if c != "sparse"]},
    "300 reviews, not 1000": {**best, "labels": 300},
    "connected components, not guarded": {**best, "cluster": "cc"},
    "rules, no model": {**best, "scorer": "rules", "labels": 0},
}
report = {"final": final}
print("final".ljust(36), " | ".join(f"seed {s}: F1 {final[s]['f1']:.4f} TP {final[s]['final']['TP']} FP {final[s]['final']['FP']} FN {final[s]['final']['FN']}" for s in seeds), flush=True)
for name, cfg in variants.items():
    res = {ds.seed: run(ds, cfg) for ds in datasets}
    report[name] = res
    print(name.ljust(36), " | ".join(
        f"seed {s}: F1 {res[s]['f1']:.4f} (Δ {res[s]['f1'] - final[s]['f1']:+.4f}) TP {res[s]['final']['TP'] - final[s]['final']['TP']:+d} FP {res[s]['final']['FP'] - final[s]['final']['FP']:+d} FN {res[s]['final']['FN'] - final[s]['final']['FN']:+d}"
        for s in seeds), flush=True)
(out / "leave_one_out.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
