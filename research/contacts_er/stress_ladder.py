"""The standardization fixes, one at a time, on the stress datasets. A fix is kept if it raises F1
on both hard datasets and costs no other dataset more than 0.001. Final pipeline throughout:
exact + set + sparse candidates (+ birth date & locality when that fix is on), logistic regression
on 1,000 actively reviewed pairs, guarded clustering.

python stress_ladder.py <stress_root> <out_json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

DATASETS = ["base7", "wild7", "dirty7", "sparse7", "hard7", "hard11"]
HARD = ("hard7", "hard11")


def run(root: Path, name: str, fixes: tuple) -> dict:
    recs = P.load(root / f"data_{name}", "full", fixes)
    key = P.load_key(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
         "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    cands = B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs)
    if "locality_key" in fixes:
        cands |= B.channel_locality(recs)
    d["pairs"] = sorted(cands)
    X = B.features(recs, d["pairs"], P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
    prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr")
    res = finish(d, prob)
    res["candidate_recall"] = round(float(y.sum() / len(d["truth"])), 4)
    res["candidates"] = len(d["pairs"])
    return res


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    kept: list[str] = [k for k in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if k]
    trying = sys.argv[4].split(",") if len(sys.argv) > 4 else list(P.FIXES)
    best = {n: run(root, n, tuple(kept)) for n in DATASETS}
    ladder = [{"step": "start: " + (",".join(kept) or "no fixes"), "results": best}]
    print(("start " + ",".join(kept)).ljust(18), " | ".join(f"{n} {best[n]['f1']:.4f}" for n in DATASETS), flush=True)
    for fix in trying:
        res = {n: run(root, n, tuple(kept + [fix])) for n in DATASETS}
        gain_hard = all(res[n]["f1"] > best[n]["f1"] for n in HARD)
        no_harm = all(res[n]["f1"] >= best[n]["f1"] - 0.001 for n in DATASETS)
        verdict = "kept" if gain_hard and no_harm else "dropped"
        ladder.append({
            "step": fix, "verdict": verdict, "results": res,
            "delta": {n: {k: res[n][k] - best[n][k] for k in ("TP", "FP", "FN")} | {"f1": round(res[n]["f1"] - best[n]["f1"], 4), "candidate_recall": round(res[n]["candidate_recall"] - best[n]["candidate_recall"], 4)} for n in DATASETS},
        })
        print(f"+ {fix:16s} {verdict:7s} " + " | ".join(
            f"{n} {res[n]['f1']:.4f} ({res[n]['f1'] - best[n]['f1']:+.4f}; FP {res[n]['FP'] - best[n]['FP']:+d} FN {res[n]['FN'] - best[n]['FN']:+d})" for n in DATASETS), flush=True)
        if verdict == "kept":
            kept.append(fix)
            best = res
    out.write_text(json.dumps({"kept": kept, "ladder": ladder}, ensure_ascii=False, indent=1))
    print("kept:", kept)
    for n in DATASETS:
        print(f"  {n:8s} F1 {best[n]['f1']:.4f}  TP {best[n]['TP']} FP {best[n]['FP']} FN {best[n]['FN']}  cand recall {best[n]['candidate_recall']}")


if __name__ == "__main__":
    main()
