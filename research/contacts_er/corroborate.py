"""Corroboration rule: name + birth date alone never auto-merge; a merge needs a second identifier
(shared phone, house, unit, locality, or a stated maiden name); uncorroborated likely matches go to
review instead. Measured at 100k with 10,000 reviews: what it saves, what it costs."""

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
    prob, lab = B.active_labels(X, y, B.score_fs(X, tf=True), 10000, batch=500, model="lr")
    corroborated = (
        (B.col(X, "phone_shared") > 0) | (B.col(X, "house_shared") > 0) | (B.col(X, "unit_shared") > 0)
        | (B.col(X, "same_locality") > 0) | (B.col(X, "alias_match") > 0)
    )
    reviewed = np.zeros(len(prob), bool)
    reviewed[lab] = True
    held = (~corroborated) & (~reviewed) & (prob >= 0.5)
    p2 = np.where(held, np.minimum(prob, 0.49), prob)
    report[name] = {
        "model": finish(d, prob),
        "corroboration rule": finish(d, p2) | {
            "sent_to_review": int(held.sum()),
            "of_which_true": int(y[held].sum()),
        },
    }
    for k, v in report[name].items():
        extra = f"  -> {v['sent_to_review']} pairs to review ({v['of_which_true']} true)" if "sent_to_review" in v else ""
        print(f"{name:9s} {k:20s} F1 {v['f1']:.4f}  P {v['p']:.4f}  R {v['r']:.4f}  TP {v['TP']}  FP {v['FP']}  FN {v['FN']}  {v['false_merges']}{extra}", flush=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
