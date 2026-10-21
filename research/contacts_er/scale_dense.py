"""Does the dense index earn its place at 100k contacts, where birth dates stop being unique?
Winner (kept fixes, deterministic + set + sparse + birth date & locality keys) vs winner + dense
(ruri, name+DOB+address, within birth year). 3,000 active reviews, guarded clustering."""

import json
import sys
import time
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
FIXES = ("kana_ocr", "readings", "split_one_field", "locality_key")
report = {}
for name in names:
    from run import vectors

    recs = P.load(root / f"data_{name}", "full", FIXES)
    key = P.load_key(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
         "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    t = time.time()
    base = B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs) | B.channel_locality(recs)
    t_base = time.time() - t
    t = time.time()
    dense = B.channel_dense(vectors(recs, "all", root / f"emb_{name}"), recs, 10, 0.85, True)
    t_dense = time.time() - t
    report[name] = {}
    for label, cands in (("winner", base), ("winner + dense", base | dense)):
        d["pairs"] = sorted(cands)
        t = time.time()
        X = B.features(recs, d["pairs"], P.frequencies(recs))
        ent = [key[r.cont_id] for r in recs]
        y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
        prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 3000, model="lr")
        r = finish(d, prob) | {"candidates": len(d["pairs"]), "candidate_recall": round(float(y.sum() / len(d["truth"])), 4),
                               "seconds": round(time.time() - t + (t_dense if "dense" in label else 0) + t_base, 1)}
        report[name][label] = r
        print(f"{name:9s} {label:15s} F1 {r['f1']:.4f}  P {r['p']:.4f}  R {r['r']:.4f}  TP {r['TP']}  FP {r['FP']}  FN {r['FN']}  cand recall {r['candidate_recall']} ({r['candidates']} pairs)  {r['seconds']}s  {r['false_merges']}", flush=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
