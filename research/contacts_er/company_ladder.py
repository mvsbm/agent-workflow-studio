"""Companies and sole proprietors: each company-specific addition must earn its place.
Final person pipeline (kept fixes, exact + set + sparse + locality keys, logistic regression on
1,000 active reviews, guarded clustering), then:
  + company keys (legal form and branch stripped, kanji and kana cores);
  + never merge a contact with a legal form and a person-like contact (legal-form cannot-link);
  vs trusting the CONT_TYPE flag for that cannot-link (it is sometimes wrong).
Reported overall and by type: people, companies, wrong person-company merges, shop -> owner links.

python company_ladder.py <root> <out_json> <dataset> [...]
"""

import json
import sys
from pathlib import Path

import numpy as np

import bench as B
import prep as P
from stress import finish

root, out, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
FIXES = ("kana_ocr", "readings", "split_one_field", "locality_key")


def legal_never(recs):
    return lambda i, j: (recs[i].company_like and B.person_like(recs[j])) or (recs[j].company_like and B.person_like(recs[i]))


def flag_never(recs):
    return lambda i, j: bool(recs[i].flag and recs[j].flag and recs[i].flag != recs[j].flag)


STEPS = [
    ("person pipeline", False, None),
    ("+ company keys", True, None),
    ("+ legal-form cannot-link", True, legal_never),
    ("flag cannot-link instead", True, flag_never),
]
report = {}
for name in names:
    recs = P.load(root / f"data_{name}", "full", FIXES)
    key = P.load_key(root / f"key_{name}")
    tags = P.load_tags(root / f"key_{name}")
    d = {"recs": recs, "key": key, "tags": tags, "hh": P.load_households(root / f"key_{name}"), "truth": P.true_pairs(recs, key)}
    base = B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs) | B.channel_locality(recs)
    company = B.channel_company(recs)
    report[name] = {}
    cache = {}
    for step, use_company, never in STEPS:
        if use_company not in cache:
            pairs = sorted(base | company) if use_company else sorted(base)
            X = B.features(recs, pairs, P.frequencies(recs))
            ent = [key[r.cont_id] for r in recs]
            y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
            prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr")
            cache[use_company] = (pairs, prob, y)
        pairs, prob, y = cache[use_company]
        d["pairs"] = pairs
        labels = B.cluster_guarded(len(recs), pairs, prob, never=never(recs) if never else None)
        r = finish(d, prob) if never is None else None
        e = B.evaluate(labels, recs, key, tags, d["hh"], d["truth"])
        t = B.by_type(labels, recs, key, tags)
        report[name][step] = {"f1": e["pairwise"]["f1"], "p": e["pairwise"]["p"], "r": e["pairwise"]["r"],
                              "candidate_recall": round(float(y.sum() / len(d["truth"])), 4), **t}
        print(f"{name:13s} {step:26s} F1 {e['pairwise']['f1']:.4f}  people {t['people']}  companies {t['companies']}  person-company merges {t['person_company_merges']}  shop->owner {t['shop_to_owner']['found']}/{t['shop_to_owner']['pairs']}", flush=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
