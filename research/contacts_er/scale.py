"""Scale: does the final pipeline hold at 100k contacts, where strangers share birth dates?
With and without the birth-date frequency feature. Times each stage.

python scale.py <stress_root> <out_json> <dataset> [...]
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

FIXES = ("kana_ocr", "readings", "split_one_field")


def main():
    root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    fixes = tuple(f for f in FIXES) + tuple(x for x in (sys.argv[0:0]))
    report = {}
    for name in names:
        t = {}
        t0 = time.time()
        recs = P.load(root / f"data_{name}", "full", fixes)
        key = P.load_key(root / f"key_{name}")
        d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
             "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
        t["load"] = round(time.time() - t0, 1)
        t0 = time.time()
        d["pairs"] = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs))
        t["candidates"] = round(time.time() - t0, 1)
        t0 = time.time()
        X = B.features(recs, d["pairs"], P.frequencies(recs))
        t["features"] = round(time.time() - t0, 1)
        ent = [key[r.cont_id] for r in recs]
        y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
        rows = {"contacts": len(recs), "true_pairs": len(d["truth"]), "candidates": len(d["pairs"]),
                "candidate_recall": round(float(y.sum() / len(d["truth"])), 4), "seconds": t}
        drop = B.FEATURES.index("dob_freq")
        keep = [i for i in range(len(B.FEATURES)) if i != drop]
        for budget in (1000, 3000):
            t0 = time.time()
            prob, lab = B.active_labels(X, y, B.score_fs(X, tf=True), budget, model="lr")
            rows[f"lr@{budget}"] = finish(d, prob) | {"seconds": round(time.time() - t0, 1)}
            p2 = B.fit_predict(X[:, keep], y, lab, "lr")
            p2[lab] = y[lab]
            rows[f"lr@{budget} without dob_freq"] = finish(d, p2)
        report[name] = rows
        print(name, {k: v for k, v in rows.items() if not isinstance(v, dict) or k == "seconds"}, flush=True)
        for k, v in rows.items():
            if isinstance(v, dict) and "f1" in v:
                print(f"   {k:28s} F1 {v['f1']:.4f}  P {v['p']:.4f}  R {v['r']:.4f}  TP {v['TP']}  FP {v['FP']}  FN {v['FN']}  {v['false_merges']}", flush=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
