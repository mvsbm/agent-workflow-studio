"""A fallible reviewer (5% of reviews wrong): what keeps one wrong review from splitting a person?
  plain   - reviews are final (a "different" review is a cannot-link);
  soft    - a review is strong evidence, not a veto;
  audit   - reviews the model (trained without them, out of fold) strongly disputes get a second
            review; the extra reviews are counted.

python review_audit.py <stress_root> <out_json> <dataset> [...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish


def main():
    root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    report = {}
    for name in names:
        recs = P.load(root / f"data_{name}", "full", ("kana_ocr", "readings", "split_one_field"))
        key = P.load_key(root / f"key_{name}")
        d = {"recs": recs, "key": key, "tags": P.load_tags(root / f"key_{name}"),
             "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
        d["pairs"] = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs))
        X = B.features(recs, d["pairs"], P.frequencies(recs))
        ent = [key[r.cont_id] for r in recs]
        y = np.array([ent[i] == ent[j] for i, j in d["pairs"]], dtype=float)
        rng = np.random.default_rng(99)
        wrong = rng.random(len(y)) < 0.05
        seen = np.where(wrong, 1 - y, y)
        rows = {}
        prob, lab = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr")
        rows["perfect reviewer"] = finish(d, prob)
        prob, lab = B.active_labels(X, seen, B.score_fs(X, tf=True), 1000, model="lr")
        rows["5% wrong: plain"] = finish(d, prob)
        model = B.fit_predict(X, seen, lab, "lr")
        soft = model.copy()
        soft[lab] = np.clip(0.8 * seen[lab] + 0.2 * model[lab], 0.15, 0.97)  # strong, never a veto
        rows["5% wrong: soft"] = finish(d, soft)
        # Out-of-fold: each reviewed pair judged by a model that never saw its review.
        lab = np.array(lab)
        folds = np.arange(len(lab)) % 5
        rng.shuffle(folds)
        oof = np.zeros(len(lab))
        for f in range(5):
            train, test = lab[folds != f], lab[folds == f]
            oof[folds == f] = B.fit_predict(X, seen, list(train), "lr")[test]
        disputed = lab[np.abs(oof - seen[lab]) > 0.7]
        second = np.where(rng.random(len(disputed)) < 0.05, 1 - y[disputed], y[disputed])
        audited = seen.copy()
        audited[disputed] = second
        prob = B.fit_predict(X, audited, list(lab), "lr")
        prob[lab] = audited[lab]
        rows["5% wrong: audit"] = finish(d, prob) | {
            "second_reviews": int(len(disputed)),
            "errors_caught": int((wrong[disputed]).sum()),
            "errors_in_reviews": int(wrong[lab].sum()),
        }
        report[name] = rows
        for k, v in rows.items():
            extra = f"  2nd reviews {v['second_reviews']}, caught {v['errors_caught']}/{v['errors_in_reviews']}" if "second_reviews" in v else ""
            print(f"{name:8s} {k:22s} F1 {v['f1']:.4f}  TP {v['TP']}  FP {v['FP']}  FN {v['FN']}{extra}", flush=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
