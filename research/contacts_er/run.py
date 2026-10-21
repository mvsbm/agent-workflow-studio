"""Run the architecture experiments: python run.py <out_dir> <data_root> [cands|score|all] [seeds...]

data_root/data<seed> and data_root/key<seed> hold each synthetic dataset (nayose_bench.contacts).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

import bench as B
import prep as P


def load_dataset(root: Path, seed: int, level: str = "full"):
    records = P.load(root / f"data{seed}", level)
    key = P.load_key(root / f"key{seed}")
    tags = P.load_tags(root / f"key{seed}")
    households = P.load_households(root / f"key{seed}")
    truth = P.true_pairs(records, key)
    return records, key, tags, households, truth


def vectors(records, kind: str, cache: Path):
    from embed import embed

    path = cache.with_suffix(f".{kind}.npy")
    if path.exists():
        return np.load(path)
    texts = [r.name_text if kind == "name" else (r.name_text + " " + r.dob + " " + r.addr_text) for r in records]
    v = embed(texts, max_length=64 if kind == "name" else 128)
    np.save(path, v)
    return v


def candidates(root: Path, out: Path, seed: int) -> dict:
    report = {}
    for level in ("raw", "full"):
        records, key, tags, hh, truth = load_dataset(root, seed, level)
        chans = {}
        chans["exact"], t1 = B.timed(B.channel_exact, records)
        chans["sets"], t2 = B.timed(B.channel_sets, records)
        chans["sparse"], t3 = B.timed(B.channel_sparse, records)
        times = {"exact": t1, "sets": t2, "sparse": t3}
        if level == "full":
            for kind in ("name", "all"):
                v, te = B.timed(vectors, records, kind, out / f"emb{seed}")
                for within in (False, True):
                    name = f"dense_{kind}{'_year' if within else ''}"
                    chans[name], td = B.timed(B.channel_dense, v, records, 10, 0.85, within)
                    times[name] = round(td + (te if not within else 0), 2)
        level_rep = {"true_pairs": len(truth), "channels": {}, "unions": {}}
        for k, s in chans.items():
            level_rep["channels"][k] = B.candidate_report(s, truth) | {"seconds": times[k]}
        combos = {
            "exact+sets": ["exact", "sets"],
            "exact+sets+sparse": ["exact", "sets", "sparse"],
            "exact+sets+sparse+dense_name": ["exact", "sets", "sparse", "dense_name"],
            "exact+sets+sparse+dense_name_year": ["exact", "sets", "sparse", "dense_name_year"],
            "exact+sets+sparse+dense_all": ["exact", "sets", "sparse", "dense_all"],
            "exact+sets+dense_name": ["exact", "sets", "dense_name"],
            "sparse+dense_name": ["sparse", "dense_name"],
            "exact+sets+dense_name_year": ["exact", "sets", "dense_name_year"],
            "exact+sets+dense_all_year": ["exact", "sets", "dense_all_year"],
            "exact+sets+dense_name_year+dense_all_year": ["exact", "sets", "dense_name_year", "dense_all_year"],
        }
        for name, parts in combos.items():
            if all(p in chans for p in parts):
                u = set().union(*(chans[p] for p in parts))
                level_rep["unions"][name] = B.candidate_report(u, truth)
        if level == "full":  # what each channel finds that no other does
            alls = set().union(*chans.values())
            level_rep["unique_true"] = {
                k: len((s - set().union(*(o for kk, o in chans.items() if kk != k and not kk.startswith(k.split('_year')[0]) ))) & truth)
                for k, s in chans.items()
            }
            missed = truth - alls
            level_rep["missed_by_all"] = len(missed)
            from collections import Counter

            c = Counter()
            for a, b in missed:
                for t in tags[records[a].cont_id] | tags[records[b].cont_id]:
                    c[t] += 1
            level_rep["missed_tags"] = dict(c.most_common(12))
        report[level] = level_rep
    return report


def scoring(root: Path, out: Path, seed: int, union=("exact", "sets", "sparse"), budgets=(100, 300, 1000)) -> dict:
    records, key, tags, hh, truth = load_dataset(root, seed)
    chans = {"exact": B.channel_exact, "sets": B.channel_sets, "sparse": B.channel_sparse}
    pairs = sorted(set().union(*(chans[c](records) for c in union if c in chans)))
    for c in union:
        if c.startswith("dense"):
            v = vectors(records, "all" if "all" in c else "name", out / f"emb{seed}")
            pairs = sorted(set(pairs) | B.channel_dense(v, records, 10, 0.85, c.endswith("_year")))
    freq = P.frequencies(records)
    X, tf = B.timed(B.features, records, pairs, freq)
    ent = [key[r.cont_id] for r in records]
    y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
    n = len(records)
    rep = {"candidates": len(pairs), "candidate_recall": round(y.sum() / len(truth), 4), "feature_seconds": tf, "runs": {}}

    def record(name, prob, labelled=None, extra=None):
        t = time.time()
        for cname, f in (("cc", B.cluster_cc), ("guarded", B.cluster_guarded), ("pivot", B.cluster_pivot)):
            labels = f(n, pairs, prob)
            res = B.evaluate(labels, records, key, tags, hh, truth)
            rep["runs"][f"{name}/{cname}"] = {
                "pairwise_f1": res["pairwise"]["f1"], "p": res["pairwise"]["p"], "r": res["pairwise"]["r"],
                "bcubed_f1": res["bcubed"]["f1"], "largest": res["largest"],
                "false_merges": res["false_merges"],
                **({"labels": len(labelled)} if labelled is not None else {}),
                **(extra or {}),
            }
            if cname == "guarded":
                rep["runs"][f"{name}/{cname}"]["recall_by_tag"] = res["recall_by_tag"]
        rep["runs"][f"{name}/cc"]["cluster_seconds"] = round(time.time() - t, 2)

    record("rules", B.score_rules(X))
    fs = B.score_fs(X)
    record("fs", fs)
    fstf = B.score_fs(X, tf=True)
    record("fs_tf", fstf)
    Xr = B.features(records, B.random_pairs(n), freq)
    record("fs_randu", B.score_fs_random_u(X, Xr))
    record("fs_randu_tf", B.score_fs_random_u(X, Xr, tf=True))
    for model in ("lr", "gbm"):
        for budget in budgets:
            prob, lab = B.active_labels(X, y, fstf, budget, model=model)
            record(f"{model}@{budget}", prob, lab)
            if model == "lr":  # the same reviewed pairs, through semi-supervised Fellegi-Sunter
                semi = B.score_fs(X, tf=True, labelled=lab, y=y)
                semi[lab] = y[lab]
                record(f"fs_semi@{budget}", semi, lab)
    return rep, X, y, pairs, records


def rerank_band(root: Path, out: Path, seed: int, budget=300, band=(0.05, 0.95)) -> dict:
    """The cross-encoder as a feature for the uncertain band only (the cost limit)."""
    from embed import rerank

    rep, X, y, pairs, records = scoring(root, out, seed, budgets=(budget,))
    key = P.load_key(root / f"key{seed}")
    tags, hh = P.load_tags(root / f"key{seed}"), P.load_households(root / f"key{seed}")
    truth = P.true_pairs(records, key)
    fstf = B.score_fs(X, tf=True)
    prob, lab = B.active_labels(X, y, fstf, budget, model="gbm")
    unsure = np.where((prob > band[0]) & (prob < band[1]))[0]
    text = lambda r: f"{r.name_text} {r.dob} {r.gender} {' '.join(sorted(r.phones))[:60]} {r.addr_text[:60]}"  # noqa: E731
    t = time.time()
    ce = np.full(len(pairs), -1.0, dtype=np.float32)
    if len(unsure):
        ce[unsure] = rerank([(text(records[pairs[k][0]]), text(records[pairs[k][1]])) for k in unsure])
    secs = round(time.time() - t, 1)
    X2 = np.hstack([X, ce[:, None]])
    prob2 = B.fit_predict(X2, y, lab, "gbm")
    prob2[lab] = y[lab]
    n = len(records)
    res = {}
    for name, p in (("gbm", prob), ("gbm+ce", prob2)):
        labels = B.cluster_guarded(n, pairs, p)
        e = B.evaluate(labels, records, key, tags, hh, truth)
        res[name] = {"pairwise_f1": e["pairwise"]["f1"], "bcubed_f1": e["bcubed"]["f1"]}
    # The cross-encoder alone on the band, as a decider.
    p3 = prob.copy()
    p3[unsure] = ce[unsure]
    e = B.evaluate(B.cluster_guarded(n, pairs, p3), records, key, tags, hh, truth)
    res["ce_decides_band"] = {"pairwise_f1": e["pairwise"]["f1"], "bcubed_f1": e["bcubed"]["f1"]}
    return {"band_pairs": int(len(unsure)), "rerank_seconds": secs, "results": res}


def transfer(root: Path, out: Path, train_seed: int, test_seed: int, budget=300) -> dict:
    """Train once with labels on one dataset, apply to another without labels."""
    _, Xa, ya, _, _ = scoring(root, out, train_seed, budgets=())
    _, Xb, yb, pairs_b, records_b = scoring(root, out, test_seed, budgets=())
    fstf = B.score_fs(Xa, tf=True)
    _, lab = B.active_labels(Xa, ya, fstf, budget, model="gbm")
    import lightgbm as lgb

    clf = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=5, verbose=-1, n_jobs=1).fit(Xa[lab], ya[lab])
    prob = clf.predict_proba(Xb)[:, 1]
    key = P.load_key(root / f"key{test_seed}")
    tags, hh = P.load_tags(root / f"key{test_seed}"), P.load_households(root / f"key{test_seed}")
    truth = P.true_pairs(records_b, key)
    e = B.evaluate(B.cluster_guarded(len(records_b), pairs_b, prob), records_b, key, tags, hh, truth)
    e_fs = B.evaluate(B.cluster_guarded(len(records_b), pairs_b, B.score_fs(Xb, tf=True)), records_b, key, tags, hh, truth)
    return {"gbm_transferred": {"pairwise_f1": e["pairwise"]["f1"], "bcubed_f1": e["bcubed"]["f1"]},
            "fs_tf_on_target": {"pairwise_f1": e_fs["pairwise"]["f1"], "bcubed_f1": e_fs["bcubed"]["f1"]}}


def main():
    out, root, what = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    seeds = [int(s) for s in sys.argv[4:]] or [7]
    out.mkdir(parents=True, exist_ok=True)
    for seed in seeds:
        if what in ("cands", "all"):
            r = candidates(root, out, seed)
            (out / f"cands{seed}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
            print(json.dumps(r, ensure_ascii=False, indent=1)[:6000])
        if what in ("score", "all"):
            import os

            union = tuple(os.environ.get("UNION", "exact,sets").split(","))
            r, *_ = scoring(root, out, seed, union)
            name = "+".join(union)
            (out / f"score{seed}_{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
            print(name, r["candidates"], r["candidate_recall"])
            for k, v in r["runs"].items():
                print(f"  {k:24s} F1 {v['pairwise_f1']:.4f} (P {v['p']:.4f} R {v['r']:.4f})  B3 {v['bcubed_f1']:.4f}  largest {v['largest']}  false {v['false_merges']}")
        if what == "rerank":
            r = rerank_band(root, out, seed)
            (out / f"rerank{seed}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
            print(json.dumps(r, indent=1))
    if what == "transfer":
        r = transfer(root, out, seeds[0], seeds[1])
        (out / "transfer.json").write_text(json.dumps(r, indent=1))
        print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
