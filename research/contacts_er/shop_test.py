"""Does the shop-name surname alias earn its place? Same pipeline with and without it."""

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P

root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
KEPT = ("kana_ocr", "readings", "split_one_field", "locality_key")
report = {}
for name in names:
    report[name] = {}
    for label, fixes in (("without", KEPT), ("with shop alias", KEPT + ("shop_alias",))):
        recs = P.load(root / f"data_{name}", "full", fixes)
        key, tags = P.load_key(root / f"key_{name}"), P.load_tags(root / f"key_{name}")
        hh, truth = P.load_households(root / f"key_{name}"), P.true_pairs(recs, key)
        pairs = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs) | B.channel_locality(recs))
        X = B.features(recs, pairs, P.frequencies(recs))
        ent = [key[r.cont_id] for r in recs]
        y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
        f1s, rows = [], []
        for seed in (0, 1, 2):
            prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr", seed=seed)
            labels = B.cluster_guarded(len(recs), pairs, prob)
            e = B.evaluate(labels, recs, key, tags, hh, truth)
            t = B.by_type(labels, recs, key, tags)
            f1s.append(e["pairwise"]["f1"])
            rows.append(t)
        r = {"f1": round(float(np.mean(f1s)), 4), "f1_std": round(float(np.std(f1s)), 4),
             "people_FP": float(np.mean([t["people"]["FP"] for t in rows])), "people_FN": float(np.mean([t["people"]["FN"] for t in rows])),
             "shop_to_owner": f"{np.mean([t['shop_to_owner']['found'] for t in rows]):.0f}/{rows[0]['shop_to_owner']['pairs']}",
             "person_company_merges": float(np.mean([t["person_company_merges"] for t in rows]))}
        report[name][label] = r
        print(f"{name:13s} {label:16s} F1 {r['f1']:.4f}±{r['f1_std']:.4f}  people FP {r['people_FP']:.0f} FN {r['people_FN']:.0f}  shop->owner {r['shop_to_owner']}  person-company merges {r['person_company_merges']:.0f}", flush=True)
out.write_text(json.dumps(report, indent=1))
