"""The proposed architecture as specified vs the measured winner, on the same data and reviews.
  proposed: raw text -> deterministic keys + sparse q-gram + dense (ruri, name+DOB+address) ->
            reranker (ruri cross-encoder, as a feature) -> decision engine (logistic regression on
            1,000 active reviews) -> connected components.
  winner:   standardization + learned fixes -> deterministic + set (phone/house) + sparse keys ->
            decision engine (same reviews, with birth-date frequency) -> guarded clustering.
  Also each missing piece added to the proposed one, so the gap is explained step by step.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
FIXES = ("kana_ocr", "readings", "split_one_field")


def run(name, level, fixes, chans, rerank, cluster):
    from embed import rerank as ce_score
    from run import vectors

    recs = P.load(root / f"data_{name}", level, fixes)
    key = P.load_key(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
         "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    cands = set()
    if "exact" in chans:
        cands |= B.channel_exact(recs)
    if "sets" in chans:
        cands |= B.channel_sets(recs)
    if "sparse" in chans:
        cands |= B.channel_sparse(recs)
    if "dense" in chans:
        cands |= B.channel_dense(vectors(recs, "all", root / f"emb_{name}_{level}"), recs, 10, 0.85, True)
    d["pairs"] = sorted(cands)
    X = B.features(recs, d["pairs"], P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
    prob, lab = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr")
    secs = 0.0
    if rerank:
        unsure = np.where((prob > 0.05) & (prob < 0.95))[0]
        text = lambda r: f"{r.name_text} {r.dob} {r.gender} {' '.join(sorted(r.phones))[:60]} {r.addr_text[:60]}"  # noqa: E731
        ce = np.full(len(d["pairs"]), -1.0, dtype=np.float32)
        t = time.time()
        if len(unsure):
            ce[unsure] = ce_score([(text(recs[d["pairs"][k][0]]), text(recs[d["pairs"][k][1]])) for k in unsure])
        secs = time.time() - t
        prob = B.fit_predict(np.hstack([X, ce[:, None]]), y, lab, "lr")
        prob[lab] = y[lab]
    res = finish(d, prob, cluster)
    res["candidate_recall"] = round(float(y.sum() / len(d["truth"])), 4)
    res["candidates"] = len(d["pairs"])
    res["rerank_seconds"] = round(secs, 1)
    return res


VARIANTS = {
    "proposed (as specified)": ("raw", (), ("exact", "sparse", "dense"), True, "cc"),
    "+ standardization": ("full", FIXES, ("exact", "sparse", "dense"), True, "cc"),
    "+ phone/house set keys": ("full", FIXES, ("exact", "sets", "sparse", "dense"), True, "cc"),
    "+ clustering stage": ("full", FIXES, ("exact", "sets", "sparse", "dense"), True, "guarded"),
    "- reranker": ("full", FIXES, ("exact", "sets", "sparse", "dense"), False, "guarded"),
    "- dense (= winner)": ("full", FIXES, ("exact", "sets", "sparse"), False, "guarded"),
}
report = {}
for name in names:
    report[name] = {}
    for v, args in VARIANTS.items():
        r = report[name][v] = run(name, *args)
        print(f"{name:8s} {v:26s} F1 {r['f1']:.4f}  P {r['p']:.4f}  R {r['r']:.4f}  TP {r['TP']}  FP {r['FP']}  FN {r['FN']}  cand recall {r['candidate_recall']} ({r['candidates']} pairs)", flush=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
