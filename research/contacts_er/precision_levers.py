"""Precision at scale: the decision threshold (precision vs recall, a business choice) and the
review budget, on 100k contacts. Winner configuration, guarded clustering."""

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
FIXES = ("kana_ocr", "readings", "split_one_field", "locality_key")
report = {}
for name in names:
    recs = P.load(root / f"data_{name}", "full", FIXES)
    key = P.load_key(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
         "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    d["pairs"] = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs) | B.channel_locality(recs))
    X = B.features(recs, d["pairs"], P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
    report[name] = {}
    for budget, batch in ((3000, 100), (10000, 500)):
        prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), budget, batch=batch, model="lr")
        for t in (0.5, 0.7, 0.85, 0.95):
            labels = B.cluster_guarded(len(recs), d["pairs"], prob, t=t)
            e = B.evaluate(labels, recs, key, d["tags"], d["hh"], d["truth"])
            r, p = e["pairwise"]["r"], e["pairwise"]["p"]
            tp = round(r * len(d["truth"]))
            fp = round(tp / p - tp) if p else 0
            row = {"f1": e["pairwise"]["f1"], "p": p, "r": r, "TP": tp, "FP": fp, "FN": len(d["truth"]) - tp, "false_merges": e["false_merges"]}
            report[name][f"{budget} reviews, threshold {t}"] = row
            print(f"{name:9s} {budget:6d} reviews  t={t:<5} F1 {row['f1']:.4f}  P {p:.4f}  R {r:.4f}  TP {tp}  FP {fp}  FN {row['FN']}  {row['false_merges']}", flush=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
