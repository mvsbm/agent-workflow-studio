"""The undecided fixes, judged on the mean of 3 active-learning seeds (luck averaged out)."""

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

root, out = Path(sys.argv[1]), Path(sys.argv[2])
KEPT = ("kana_ocr", "readings", "split_one_field")
TRY = ["clean_fields", "placeholder_dob", "locality_key"]
DATASETS = ["base7", "wild7", "dirty7", "sparse7", "hard7", "hard11"]


def run(name, fixes, seeds=(0, 1, 2)):
    recs = P.load(root / f"data_{name}", "full", fixes)
    key = P.load_key(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
         "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    c = B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs)
    if "locality_key" in fixes:
        c |= B.channel_locality(recs)
    d["pairs"] = sorted(c)
    X = B.features(recs, d["pairs"], P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
    runs = [finish(d, B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr", seed=s)[0]) for s in seeds]
    return {k: round(float(np.mean([r[k] for r in runs])), 4) for k in ("f1", "TP", "FP", "FN")} | {"f1_std": round(float(np.std([r["f1"] for r in runs])), 4)}


base = {n: run(n, KEPT) for n in DATASETS}
report = {"kept": base}
print("kept".ljust(16), " | ".join(f"{n} {base[n]['f1']:.4f}±{base[n]['f1_std']:.4f}" for n in DATASETS), flush=True)
for fix in TRY:
    res = {n: run(n, KEPT + (fix,)) for n in DATASETS}
    report[fix] = res
    print(f"+ {fix:14s}", " | ".join(
        f"{n} {res[n]['f1']:.4f} ({res[n]['f1'] - base[n]['f1']:+.4f}; FP {res[n]['FP'] - base[n]['FP']:+.0f} FN {res[n]['FN'] - base[n]['FN']:+.0f})" for n in DATASETS), flush=True)
out.write_text(json.dumps(report, indent=1))
