"""Company improvements, one at a time, each kept only if it raises overall F1 on both hard
company datasets by more than the noise (the standard error of the difference of SEEDS-seed means,
default 3) and costs no dataset more than its noise.

python company_trials.py <root> <out_json> [previous_json]  (resume: start from its final config)
"""

import json
import os
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P

root, out = Path(sys.argv[1]), Path(sys.argv[2])
DATASETS = ["company7", "company11", "company_hard7", "company_hard11"]
HARD = ("company_hard7", "company_hard11")
BASE_FIXES = ("kana_ocr", "readings", "split_one_field", "locality_key", "shop_alias")
STEPS = [
    ("company field cleaning", {"fixes": ("company_clean",)}),
    ("company keys", {"company_keys": True}),
    ("core-equality feature", {"core_eq": True}),
    ("dense on companies", {"company_dense": True}),
    ("same core and founding date", {"core_dob": True}),  # a moved company keeps both
    ("shared phone/house, different core", {"shared_not_core": True}),  # absorbed and group companies
]


def run(name, cfg):
    recs = P.load(root / f"data_{name}", "full", BASE_FIXES + cfg.get("fixes", ()))
    key, tags = P.load_key(root / f"key_{name}"), P.load_tags(root / f"key_{name}")
    hh, truth = P.load_households(root / f"key_{name}"), P.true_pairs(recs, key)
    cands = B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs) | B.channel_locality(recs)
    if cfg.get("company_keys"):
        cands |= B.channel_company(recs)
    if cfg.get("company_dense"):
        cands |= B.channel_company_dense(recs)
    pairs = sorted(cands)
    X = B.features(recs, pairs, P.frequencies(recs))
    if not cfg.get("core_eq"):
        X = X[:, [i for i, f in enumerate(B.FEATURES) if f != "core_eq"]]
    c = lambda f: B.col(X, f)  # noqa: E731
    if cfg.get("core_dob"):
        X = np.column_stack([X, c("core_eq") * c("dob_exact")])
    if cfg.get("shared_not_core"):
        X = np.column_stack([X, c("both_company") * (1 - c("core_eq")) * ((c("phone_shared") + c("house_shared")) > 0)])
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
    res = []
    for seed in range(int(os.environ.get("SEEDS", 3))):
        prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr", seed=seed)
        labels = B.cluster_guarded(len(recs), pairs, prob)
        e = B.evaluate(labels, recs, key, tags, hh, truth)
        t = B.by_type(labels, recs, key, tags)
        res.append((e["pairwise"]["f1"], t))
    mean = lambda f: float(np.mean([f(r) for r in res]))  # noqa: E731
    return {
        "f1": round(mean(lambda r: r[0]), 4),
        "f1_std": round(float(np.std([r[0] for r in res])), 4),
        "companies": {k: round(mean(lambda r, k=k: r[1]["companies"][k]), 4 if k == "f1" else 1) for k in ("f1", "TP", "FP", "FN")},
        "people": {k: round(mean(lambda r, k=k: r[1]["people"][k]), 4 if k == "f1" else 1) for k in ("f1", "TP", "FP", "FN")},
        "person_company_merges": mean(lambda r: r[1]["person_company_merges"]),
        "candidate_recall": round(float(y.sum() / len(truth)), 4),
        "candidates": len(pairs),
    }


def line(name, r):
    c, p = r["companies"], r["people"]
    return (f"{name} F1 {r['f1']:.4f}±{r['f1_std']:.4f}  companies F1 {c['f1']:.4f} (TP {c['TP']:.0f} FP {c['FP']:.0f} FN {c['FN']:.0f})"
            f"  people F1 {p['f1']:.4f}  p-c merges {r['person_company_merges']:.0f}  cand recall {r['candidate_recall']}")


prev = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else {"final": {}, "log": []}
cfg = {**prev["final"], "fixes": tuple(prev["final"].get("fixes", ()))}
done = {s["step"] for s in prev["log"]}
best = {n: run(n, cfg) for n in DATASETS}
log = prev["log"] + [{"step": "start" if not done else "resume", "config": dict(cfg), "results": best}]
print("start")
for n in DATASETS:
    print("  " + line(n, best[n]), flush=True)
for step, change in [s for s in STEPS if s[0] not in done]:
    trial = {**cfg, **change, "fixes": cfg.get("fixes", ()) + change.get("fixes", ())}
    res = {n: run(n, trial) for n in DATASETS}
    se = {n: (res[n]["f1_std"] ** 2 + best[n]["f1_std"] ** 2) ** 0.5 / int(os.environ.get("SEEDS", 3)) ** 0.5 for n in DATASETS}
    gain = all(res[n]["f1"] - best[n]["f1"] > se[n] for n in HARD)
    harm = any(res[n]["f1"] < best[n]["f1"] - max(0.001, 2 * best[n]["f1_std"]) for n in DATASETS)
    verdict = "kept" if gain and not harm else "dropped"
    log.append({"step": step, "verdict": verdict, "config": trial, "results": res})
    print(f"+ {step}: {verdict}")
    for n in DATASETS:
        print("  " + line(n, res[n]) + f"  (ΔF1 {res[n]['f1'] - best[n]['f1']:+.4f}, companies FP {res[n]['companies']['FP'] - best[n]['companies']['FP']:+.0f} FN {res[n]['companies']['FN'] - best[n]['companies']['FN']:+.0f})", flush=True)
    if verdict == "kept":
        cfg, best = trial, res
out.write_text(json.dumps({"final": cfg, "log": log}, ensure_ascii=False, indent=1))
