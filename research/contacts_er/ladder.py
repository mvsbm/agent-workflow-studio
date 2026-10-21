"""The ablation ladder: start from the simplest pipeline, add one component at a time, and keep it
only if it improves the final pairwise F1 on every dataset. Each step reports the confusion matrix
at the three stages a component can change: candidates, pair decisions, final clusters.

python ladder.py <out_dir> <data_root> <seed> [<seed> ...]
"""

from __future__ import annotations

import json
import sys
import time
from math import comb
from pathlib import Path

import numpy as np

import bench as B
import prep as P

# Each step: (name, what it changes, the config change). Applied on top of the best config so far.
STEPS = [
    ("baseline", "raw text, DOB+name keys, hand rules, connected components", {}),
    ("standardize", "kanji variants, kana, phones, addresses parsed", {"level": "full"}),
    ("set keys", "candidates sharing a phone or a house", {"chans": ["exact", "sets"]}),
    ("sparse q-gram", "name bigram kNN candidates", {"chans": "+sparse"}),
    ("dense", "ruri embedding kNN (name+DOB+address, within birth year)", {"chans": "+dense_all_year"}),
    ("Fellegi-Sunter", "EM scoring, no labels (replaces rules)", {"scorer": "fs"}),
    ("FS random-u", "Splink-style u from random pairs", {"scorer": "fs_randu"}),
    ("review 300 + FS", "300 reviewed pairs, semi-supervised FS (studio Learn weights)", {"scorer": "fs_semi", "labels": 300}),
    ("review 300 + LR", "300 reviewed pairs, logistic regression", {"scorer": "lr", "labels": 300}),
    ("review 300 + GBM", "300 reviewed pairs, gradient boosting", {"scorer": "gbm", "labels": 300}),
    ("review 1000", "1000 reviewed pairs", {"labels": 1000}),
    ("guarded clustering", "cannot-link + size cap (replaces connected components)", {"cluster": "guarded"}),
    ("cross-encoder", "ruri reranker score on the uncertain band, as a feature", {"rerank": True}),
]
BASE = {"level": "raw", "chans": ["exact"], "scorer": "rules", "labels": 0, "cluster": "cc", "rerank": False}


class Dataset:
    def __init__(self, root: Path, out: Path, seed: int):
        self.root, self.out, self.seed = root, out, seed
        self.cache = {}

    def records(self, level):
        if ("rec", level) not in self.cache:
            recs = P.load(self.root / f"data{self.seed}", level)
            key = P.load_key(self.root / f"key{self.seed}")
            self.cache["rec", level] = (
                recs,
                key,
                P.load_tags(self.root / f"key{self.seed}"),
                P.load_households(self.root / f"key{self.seed}"),
                P.true_pairs(recs, key),
            )
        return self.cache["rec", level]

    def channel(self, level, name):
        if ("ch", level, name) not in self.cache:
            recs = self.records(level)[0]
            if name == "exact":
                v = B.channel_exact(recs)
            elif name == "sets":
                v = B.channel_sets(recs)
            elif name == "sparse":
                v = B.channel_sparse(recs)
            else:
                from run import vectors

                vec = vectors(recs, "all" if "all" in name else "name", self.out / f"emb{self.seed}")
                v = B.channel_dense(vec, recs, 10, 0.85, name.endswith("_year"))
            self.cache["ch", level, name] = v
        return self.cache["ch", level, name]


def run(ds: Dataset, cfg: dict) -> dict:
    t0 = time.time()
    recs, key, tags, hh, truth = ds.records(cfg["level"])
    n = len(recs)
    pairs = sorted(set().union(*(ds.channel(cfg["level"], c) for c in cfg["chans"])))
    freq = P.frequencies(recs)
    X = B.features(recs, pairs, freq)
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
    s, lab = cfg["scorer"], None
    if s == "rules":
        prob = B.score_rules(X)
    elif s == "fs":
        prob = B.score_fs(X, tf=True)
    elif s == "fs_randu":
        prob = B.score_fs_random_u(X, B.features(recs, B.random_pairs(n), freq), tf=True)
    elif s == "fs_semi":
        _, lab = B.active_labels(X, y, B.score_fs(X, tf=True), cfg["labels"], model="lr")
        prob = B.score_fs(X, tf=True, labelled=lab, y=y)
        prob[lab] = y[lab]
    else:
        prob, lab = B.active_labels(X, y, B.score_fs(X, tf=True), cfg["labels"], model=s)
    rerank_secs = 0.0
    if cfg["rerank"] and lab is not None:
        from embed import rerank

        unsure = np.where((prob > 0.05) & (prob < 0.95))[0]
        text = lambda r: f"{r.name_text} {r.dob} {r.gender} {' '.join(sorted(r.phones))[:60]} {r.addr_text[:60]}"  # noqa: E731
        ce = np.full(len(pairs), -1.0, dtype=np.float32)
        t = time.time()
        if len(unsure):
            ce[unsure] = rerank([(text(recs[pairs[k][0]]), text(recs[pairs[k][1]])) for k in unsure])
        rerank_secs = time.time() - t
        prob = B.fit_predict(np.hstack([X, ce[:, None]]), y, lab, s if s in ("lr", "gbm") else "gbm")
        prob[lab] = y[lab]
    labels = (B.cluster_guarded if cfg["cluster"] == "guarded" else B.cluster_cc)(n, pairs, prob)
    found = set(pairs)
    tp_c = len(found & truth)
    decided = {p for p, v in zip(pairs, prob, strict=True) if v >= 0.5}
    tp_d = len(decided & truth)
    final = B.evaluate(labels, recs, key, tags, hh, truth)
    # Final confusion over all pairs, from precision/recall and the truth size.
    r, p = final["pairwise"]["r"], final["pairwise"]["p"]
    tp = round(r * len(truth))
    fp = round(tp / p - tp) if p else 0
    fn = len(truth) - tp
    return {
        "candidates": {"TP": tp_c, "FP": len(found) - tp_c, "FN": len(truth) - tp_c},
        "decisions": {"TP": tp_d, "FP": len(decided) - tp_d, "FN": len(truth) - tp_d},
        "final": {"TP": tp, "FP": fp, "FN": fn, "TN": comb(n, 2) - tp - fp - fn},
        "f1": final["pairwise"]["f1"],
        "precision": p,
        "recall": r,
        "bcubed_f1": final["bcubed"]["f1"],
        "false_merges": final["false_merges"],
        "labels": len(lab) if lab is not None else 0,
        "seconds": round(time.time() - t0, 1),
        "rerank_seconds": round(rerank_secs, 1),
    }


def apply(cfg: dict, change: dict) -> dict:
    new = dict(cfg)
    for k, v in change.items():
        new[k] = new[k] + [v[1:]] if k == "chans" and isinstance(v, str) else v
    if change.get("scorer") in ("lr", "gbm", "fs_semi") and not new["labels"]:
        new["labels"] = 300
    return new


def main():
    out, root, seeds = Path(sys.argv[1]), Path(sys.argv[2]), [int(s) for s in sys.argv[3:]]
    datasets = [Dataset(root, out, s) for s in seeds]
    best_cfg, best = dict(BASE), None
    ladder = []
    for name, what, change in STEPS:
        cfg = apply(best_cfg, change) if best is not None else dict(BASE)
        results = {ds.seed: run(ds, cfg) for ds in datasets}
        if best is None:
            kept, verdict = True, "baseline"
        else:
            gains = [results[s]["f1"] - best[s]["f1"] for s in seeds]
            kept = all(g > 0 for g in gains)
            verdict = "kept" if kept else "dropped"
        step = {"step": name, "what": what, "config": cfg, "verdict": verdict, "results": results}
        if best is not None:
            step["delta"] = {
                s: {
                    stage: {k: results[s][stage][k] - best[s][stage][k] for k in results[s][stage]}
                    for stage in ("candidates", "decisions", "final")
                }
                | {"f1": round(results[s]["f1"] - best[s]["f1"], 4)}
                for s in seeds
            }
        ladder.append(step)
        if kept:
            best_cfg, best = cfg, results
        line = " | ".join(
            f"seed {s}: F1 {results[s]['f1']:.4f} TP {results[s]['final']['TP']} FP {results[s]['final']['FP']} FN {results[s]['final']['FN']}"
            for s in seeds
        )
        print(f"{name:22s} {verdict:8s} {line}", flush=True)
    # A component dropped early may earn its place on the final pipeline: try each once more.
    for step in [s for s in ladder if s["verdict"] == "dropped"]:
        change = next(c for n, _, c in STEPS if n == step["step"])
        cfg = apply(best_cfg, change)
        if cfg == best_cfg:
            continue
        results = {ds.seed: run(ds, cfg) for ds in datasets}
        kept = all(results[s]["f1"] > best[s]["f1"] for s in seeds)
        ladder.append(
            {
                "step": step["step"] + " (retried on the final pipeline)",
                "what": step["what"],
                "config": cfg,
                "verdict": "kept" if kept else "dropped",
                "results": results,
                "delta": {
                    s: {
                        stage: {k: results[s][stage][k] - best[s][stage][k] for k in results[s][stage]}
                        for stage in ("candidates", "decisions", "final")
                    }
                    | {"f1": round(results[s]["f1"] - best[s]["f1"], 4)}
                    for s in seeds
                },
            }
        )
        print(f"{step['step'] + ' (retry)':22s} {'kept' if kept else 'dropped':8s} "
              + " | ".join(f"seed {s}: F1 {results[s]['f1']:.4f}" for s in seeds), flush=True)
        if kept:
            best_cfg, best = cfg, results
    (out / "ladder.json").write_text(json.dumps(ladder, ensure_ascii=False, indent=1))
    (out / "ladder_best.json").write_text(json.dumps(best_cfg, indent=1))


if __name__ == "__main__":
    main()
