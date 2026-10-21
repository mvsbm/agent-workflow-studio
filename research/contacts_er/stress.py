"""Stress the final pipeline: harder dirt, more blanks, more look-alikes, unseen CRM mess, a
fallible reviewer, a model trained on easy data used on hard data. Every row: the final pairwise
confusion counts.

python stress.py <stress_root> <out_json> [level]   (level: full = the current standardization)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

import bench as B
import prep as P

DATASETS = ["base7", "dirty7", "sparse7", "lookalike7", "wild7", "hard7", "hard11"]


def prepare(root: Path, name: str, level: str):
    recs = P.load(root / f"data_{name}", level)
    key = P.load_key(root / f"key_{name}")
    tags, hh = P.load_tags(root / f"key_{name}"), P.load_households(root / f"key_{name}")
    truth = P.true_pairs(recs, key)
    t = time.time()
    pairs = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs))
    X = B.features(recs, pairs, P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
    return {
        "recs": recs, "key": key, "tags": tags, "hh": hh, "truth": truth, "pairs": pairs,
        "X": X, "y": y, "cand_recall": y.sum() / len(truth), "prep_seconds": round(time.time() - t, 1),
    }


def finish(d, prob, cluster="guarded"):
    n = len(d["recs"])
    labels = (B.cluster_guarded if cluster == "guarded" else B.cluster_cc)(n, d["pairs"], prob)
    e = B.evaluate(labels, d["recs"], d["key"], d["tags"], d["hh"], d["truth"])
    r, p = e["pairwise"]["r"], e["pairwise"]["p"]
    tp = round(r * len(d["truth"]))
    fp = round(tp / p - tp) if p else 0
    return {
        "f1": e["pairwise"]["f1"], "p": p, "r": r, "TP": tp, "FP": fp, "FN": len(d["truth"]) - tp,
        "false_merges": e["false_merges"],
        "weak_tags": {t: v for t, v in e["recall_by_tag"].items() if v < 0.97},
    }


def reviewed(d, budget, noise=0.0, model="lr", seed=0):
    y_seen = d["y"].copy()
    if noise:  # a fallible reviewer: some reviews are wrong
        flip = np.random.default_rng(seed + 99).random(len(y_seen)) < noise
        y_seen[flip] = 1 - y_seen[flip]
    base = B.score_fs(d["X"], tf=True)
    prob, lab = B.active_labels(d["X"], y_seen, base, budget, model=model)
    return prob, lab


def main():
    root, out, level = Path(sys.argv[1]), Path(sys.argv[2]), (sys.argv[3] if len(sys.argv) > 3 else "full")
    report = {}
    cache = {}
    for name in DATASETS:
        d = cache[name] = prepare(root, name, level)
        rows = {"candidate_recall": round(float(d["cand_recall"]), 4), "candidates": len(d["pairs"])}
        rows["rules"] = finish(d, B.score_rules(d["X"]))
        for budget in (300, 1000):
            prob, _ = reviewed(d, budget)
            rows[f"lr@{budget}"] = finish(d, prob)
        prob, _ = reviewed(d, 1000, noise=0.05)
        rows["lr@1000, 5% wrong reviews"] = finish(d, prob)
        report[name] = rows
        print(name, f"cand_recall {rows['candidate_recall']}", flush=True)
        for k, v in rows.items():
            if isinstance(v, dict):
                print(f"   {k:28s} F1 {v['f1']:.4f}  TP {v['TP']}  FP {v['FP']}  FN {v['FN']}  {v['false_merges']}", flush=True)
        print("   weak:", rows["lr@1000"]["weak_tags"], flush=True)
    # Shift: reviews collected on base data, model used on hard data with no new reviews.
    b, h = cache["base7"], cache["hard7"]
    _, lab = reviewed(b, 1000)
    w = B.fit_predict(np.vstack([b["X"], h["X"]]), np.concatenate([b["y"], h["y"]]), list(lab), "lr")
    report["shift: base reviews -> hard7"] = finish(h, w[len(b["X"]) :])
    v = report["shift: base reviews -> hard7"]
    print(f"shift base->hard7  F1 {v['f1']:.4f}  TP {v['TP']}  FP {v['FP']}  FN {v['FN']}", flush=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
