"""Which feature set does the final pipeline need? Groups dropped together, on the final candidates."""

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from ablate import GROUPS, confusion

out, root, seeds = Path(sys.argv[1]), Path(sys.argv[2]), [int(s) for s in sys.argv[3:]]
SETS = {
    "all": [],
    "no names at all": ["kana name", "kanji name", "name structure", "name frequency"],
    "kana only (no kanji)": ["kanji name"],
    "kanji only (no kana)": ["kana name"],
    "no gender": ["gender"],
    "no gender, no name frequency": ["gender", "name frequency"],
    "no phones, no addresses": ["phones", "addresses"],
}
report = {}
for seed in seeds:
    recs = P.load(root / f"data{seed}")
    key, tags = P.load_key(root / f"key{seed}"), P.load_tags(root / f"key{seed}")
    hh, truth = P.load_households(root / f"key{seed}"), P.true_pairs(recs, key)
    pairs = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs))
    X = B.features(recs, pairs, P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
    n = len(recs)
    _, lab = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr")
    rep = {}
    for name, drop in SETS.items():
        cols = {c for g in drop for c in GROUPS[g]}
        keep = [i for i, f in enumerate(B.FEATURES) if f not in cols]
        prob = B.fit_predict(X[:, keep], y, lab, "lr")
        prob[lab] = y[lab]
        labels = B.cluster_guarded(n, pairs, prob)
        rep[name] = confusion(labels, recs, key, tags, hh, truth)
        if name == "all":
            e = B.evaluate(labels, recs, key, tags, hh, truth)
            rep["all"]["missed_by_tag"] = {t: v for t, v in e["recall_by_tag"].items() if v < 0.99}
        print(f"seed {seed}  {name:30s} F1 {rep[name]['f1']:.4f}  TP {rep[name]['TP']}  FP {rep[name]['FP']}  FN {rep[name]['FN']}  false {rep[name]['false_merges']}", flush=True)
    print("   recall < 0.99 by tag:", rep["all"]["missed_by_tag"], flush=True)
    report[seed] = rep
(out / "features_min.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
