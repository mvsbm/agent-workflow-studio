"""What company errors remain on the improved pipeline? Missed pairs by dirt tag, false merges by
the kind of look-alike (same-name stranger, sibling in one building, absorbed company)."""

import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import numpy as np

import bench as B
import prep as P

root, names = Path(sys.argv[1]), sys.argv[2:]
FIXES = ("kana_ocr", "readings", "split_one_field", "locality_key", "shop_alias", "company_clean")
for name in names:
    recs = P.load(root / f"data_{name}", "full", FIXES)
    key, tags = P.load_key(root / f"key_{name}"), P.load_tags(root / f"key_{name}")
    hh, truth = P.load_households(root / f"key_{name}"), P.true_pairs(recs, key)
    pairs = sorted(B.channel_exact(recs) | B.channel_sets(recs) | B.channel_sparse(recs) | B.channel_locality(recs))
    X = B.features(recs, pairs, P.frequencies(recs))
    ent = [key[r.cont_id] for r in recs]
    y = np.array([ent[i] == ent[j] for i, j in pairs], dtype=float)
    prob, _ = B.active_labels(X, y, B.score_fs(X, tf=True), 1000, model="lr")
    labels = B.cluster_guarded(len(recs), pairs, prob)
    by = defaultdict(list)
    for i, lab in enumerate(labels):
        by[lab].append(i)
    pred = {p for ids in by.values() for p in combinations(sorted(ids), 2)}
    comp = lambda i: ent[i].startswith("C")  # noqa: E731
    cand = set(pairs)
    fn, fp = Counter(), Counter()
    for a, b in truth - pred:
        if comp(a) and comp(b):
            where = "not a candidate" if (a, b) not in cand else "scored apart"
            for t in (tags[recs[a].cont_id] | tags[recs[b].cont_id]) - {"company"}:
                fn[(where, t)] += 1
    for a, b in pred - truth:
        if comp(a) and comp(b):
            kinds = (tags[recs[a].cont_id] | tags[recs[b].cont_id]) & {"same_name_company", "group_company", "merged_away"}
            fp[",".join(sorted(kinds)) or "other"] += 1
    print(name, "company FN:", sum(1 for a, b in truth - pred if comp(a) and comp(b)), "FP:", sum(fp.values()))
    print("  missed:", [(f"{w}/{t}", c) for (w, t), c in fn.most_common(12)])
    print("  false merges:", dict(fp))
    ex = [(a, b) for a, b in pred - truth if comp(a) and comp(b)][:4]
    for a, b in ex:
        print("   FP", recs[a].sur_k, "|", recs[b].sur_k, "| dob", recs[a].dob, recs[b].dob, "| phones", bool(recs[a].phones & recs[b].phones), "| house", bool(recs[a].houses & recs[b].houses))
