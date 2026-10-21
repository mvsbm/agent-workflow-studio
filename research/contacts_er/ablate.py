"""Does each part of the decision engine earn its place? On the best candidate set:
  labels  - actively chosen vs random reviewed pairs (same count);
  day 0   - no reviews: rules' confident decisions as training labels (weak supervision);
  features - drop one feature group at a time from the model.
Each result carries the final pairwise confusion counts.

python ablate.py <out_dir> <data_root> <seed> [<seed> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P

GROUPS = {
    "kana name": ["jw_full_n", "jw_sur_n", "jw_giv_n", "eq_loose"],
    "kanji name": ["jw_full_k", "eq_sur_k", "eq_giv_k"],
    "name structure": ["swap_name", "giv_match", "sur_match", "name_missing"],
    "birth date": ["dob_exact", "dob_swapped", "dob_digits", "dob_year_diff"],
    "gender": ["gender"],
    "phones": ["phone_shared", "phone_min_freq", "phone_both"],
    "addresses": ["house_shared", "unit_shared", "house_min_freq", "addr_best_jw", "same_locality", "addr_both"],
    "name frequency": ["rare_sur", "rare_giv", "rare_full"],
}


def confusion(labels, recs, key, tags, hh, truth):
    e = B.evaluate(labels, recs, key, tags, hh, truth)
    r, p = e["pairwise"]["r"], e["pairwise"]["p"]
    tp = round(r * len(truth))
    fp = round(tp / p - tp) if p else 0
    return {"f1": e["pairwise"]["f1"], "TP": tp, "FP": fp, "FN": len(truth) - tp, "false_merges": e["false_merges"]}


def main():
    out, root, seeds = Path(sys.argv[1]), Path(sys.argv[2]), [int(s) for s in sys.argv[3:]]
    report = {}
    for seed in seeds:
        recs = P.load(root / f"data{seed}")
        key, tags = P.load_key(root / f"key{seed}"), P.load_tags(root / f"key{seed}")
        hh, truth = P.load_households(root / f"key{seed}"), P.true_pairs(recs, P.load_key(root / f"key{seed}"))
        pairs = sorted(B.channel_exact(recs) | B.channel_sets(recs))
        X = B.features(recs, pairs, P.frequencies(recs))
        ent = [key[r.cont_id] for r in recs]
        y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
        n, base = len(recs), B.score_fs(X, tf=True)
        cluster = lambda prob: confusion(B.cluster_guarded(n, pairs, prob), recs, key, tags, hh, truth)  # noqa: E731
        rep = {}
        for budget in (300, 1000):
            prob, lab = B.active_labels(X, y, base, budget, model="lr")
            rep[f"active {budget}"] = cluster(prob)
            rng = np.random.default_rng(1)
            rand = [int(i) for i in rng.choice(len(X), size=budget, replace=False)]
            p2 = B.fit_predict(X, y, rand, "lr")
            p2[rand] = y[rand]
            rep[f"random {budget}"] = cluster(p2)
        # Day 0: rules say match -> 1; a birth-date conflict with no shared phone/house -> 0.
        rules = B.score_rules(X)
        conflict = (B.col(X, "dob_digits") >= 3) & (B.col(X, "phone_shared") + B.col(X, "house_shared") == 0)
        weak = np.where(rules > 0)[0].tolist() + np.where(conflict)[0].tolist()
        yw = y.copy()
        yw[np.where(rules > 0)[0]] = 1
        yw[np.where(conflict)[0]] = 0
        rep["day 0: rules only"] = cluster(rules)
        rep["day 0: weak labels -> LR"] = cluster(B.fit_predict(X, yw, weak, "lr"))
        # Feature groups, each dropped from the 1000-label model.
        _, lab = B.active_labels(X, y, base, 1000, model="lr")
        full = B.fit_predict(X, y, lab, "lr")
        full[lab] = y[lab]
        rep["all features (LR 1000)"] = cluster(full)
        for g, cols in GROUPS.items():
            keep = [i for i, f in enumerate(B.FEATURES) if f not in cols]
            p3 = B.fit_predict(X[:, keep], y, lab, "lr")
            p3[lab] = y[lab]
            rep[f"without {g}"] = cluster(p3)
        report[seed] = rep
        for k, v in rep.items():
            print(f"seed {seed}  {k:28s} F1 {v['f1']:.4f}  TP {v['TP']}  FP {v['FP']}  FN {v['FN']}", flush=True)
    (out / "ablate.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
