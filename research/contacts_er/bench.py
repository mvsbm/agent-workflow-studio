"""Architectures for contact matching, measured on synthetic data with an answer key.

Stages, each with variants:
  candidates  - exact keys (DOB + name parts), set keys (shared phone / house), sparse q-gram kNN,
                dense kNN (local ruri embeddings), with purging of oversized key groups.
  features    - per field, script-aware, set-aware, frequency-aware comparison values.
  scorers     - hand rules; Fellegi-Sunter EM (with/without term frequency); logistic regression
                and gradient boosting on actively chosen labels; cross-encoder rerank feature.
  clustering  - connected components; guarded merging (cannot-link, size cap); pivot.
Metrics: candidate recall and count; pairwise and B-cubed precision/recall/F1; recall per dirt
tag; false merges by kind; seconds per stage.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from itertools import combinations

import numpy as np

from prep import Record, frequencies


# --- string similarity --------------------------------------------------------------------------
def jaro_winkler(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    window = max(len(a), len(b)) // 2 - 1
    ma, mb = [False] * len(a), [False] * len(b)
    m = 0
    for i, c in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not mb[j] and b[j] == c:
                ma[i] = mb[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t, k = 0, 0
    for i, c in enumerate(a):
        if ma[i]:
            while not mb[k]:
                k += 1
            t += c != b[k]
            k += 1
    jaro = (m / len(a) + m / len(b) + (m - t / 2) / m) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def bigrams(s: str) -> set:
    s = f"^{s}$"
    return {s[i : i + 2] for i in range(len(s) - 1)}


# --- candidates ------------------------------------------------------------------------------------
def key_pairs(groups: dict, cap: int) -> set:
    """Pairs within each key group; groups larger than cap are purged (a common key pairs no one)."""
    out = set()
    for ids in groups.values():
        if 1 < len(ids) <= cap:
            ids = sorted(ids)
            out.update(combinations(ids, 2))
    return out


def group(records: list[Record], keyf) -> dict:
    g = defaultdict(set)
    for i, r in enumerate(records):
        for k in keyf(r):
            if k:
                g[k].add(i)
    return g


def swapped_md(dob: str) -> str:
    y, m, d = (dob.split("-") + ["", ""])[:3]
    return f"{y}-{d}-{m}" if m and d and m != d else ""


def channel_exact(records, cap=50) -> set:
    """DOB with each name part (kana and kanji), and the full name alone (a DOB typo still meets)."""
    keys = [
        lambda r: [r.dob + "|n|" + r.full_n] if r.full_n and r.dob else [],
        lambda r: [r.dob + "|k|" + r.full_k] if r.full_k and r.dob else [],
        lambda r: [r.dob + "|gn|" + r.giv_n] if r.giv_n and r.dob else [],  # married: given + DOB
        lambda r: [r.dob + "|gk|" + r.giv_k] if r.giv_k and r.dob else [],
        lambda r: [r.dob + "|sn|" + r.sur_n] if r.sur_n and r.dob else [],
        lambda r: [r.dob + "|sk|" + s for s in {r.sur_k} | r.aliases if s] if r.dob else [],
        lambda r: [swapped_md(r.dob) + "|n|" + r.full_n] if r.full_n and r.dob else [],  # swapped
        lambda r: ["L|" + r.loose_n] if len(r.loose_n) >= 4 else [],
        lambda r: ["K|" + r.full_k] if len(r.full_k) >= 3 else [],
    ]
    out = set()
    # A contact whose birth date is unknown meets every contact with its full name (either script).
    for field in ("full_n", "full_k"):
        names = group(records, lambda r: [getattr(r, field)] if len(getattr(r, field)) >= 3 else [])
        for ids in names.values():
            unknown = [i for i in ids if not records[i].dob]
            if unknown and len(ids) <= 200:
                out.update((min(i, j), max(i, j)) for i in unknown for j in ids if i != j)
    for f in keys:
        g = group(records, f)
        # The swapped-DOB key joins with the plain DOB key of the other record.
        if f is keys[6]:
            g2 = group(records, keys[0])
            for k, ids in g.items():
                if k in g2:
                    both = ids | g2[k]
                    if len(both) <= cap:
                        out.update(combinations(sorted(both), 2))
            continue
        out |= key_pairs(g, cap)
    return out


def channel_locality(records, cap=20) -> set:
    """Birth date + a shared municipality/town: a contact with no name meets the same person."""
    return key_pairs(group(records, lambda r: [r.dob + "|" + loc for loc in r.localities] if r.dob else []), cap)


def channel_company(records, cap=50) -> set:
    """Company name cores (legal form and branch stripped), kanji and kana."""
    return key_pairs(group(records, lambda r: [k for k in ("K|" + r.core_k if len(r.core_k) >= 2 else "", "N|" + r.core_n if len(r.core_n) >= 3 else "") if k]), cap)


def channel_company_dense(records, k=10, least=0.88, cache=None) -> set:
    """Dense kNN among company-like records only, on the company core (kanji and kana)."""
    from embed import embed

    ids = [i for i, r in enumerate(records) if r.core_k or r.core_n]
    if len(ids) < 2:
        return set()
    v = embed([f"{records[i].core_k} {records[i].core_n}".strip() for i in ids], max_length=48)
    out = set()
    for start in range(0, len(ids), 2048):
        sims = v[start : start + 2048] @ v.T
        for row in range(sims.shape[0]):
            sims[row, start + row] = -1
        kk = min(k, len(ids) - 1)
        top = np.argpartition(-sims, kk, axis=1)[:, :kk]
        for row in range(sims.shape[0]):
            i = ids[start + row]
            for c in top[row]:
                if sims[row, c] >= least:
                    j = ids[c]
                    out.add((min(i, j), max(i, j)))
    return out


def person_like(r) -> bool:
    return bool(r.giv_k or r.giv_n) and not r.company_like and not r.shop_like


def channel_sets(records, cap=20) -> set:
    """Shared phone, shared house (block-level address): a family shares them, so they are capped."""
    return key_pairs(group(records, lambda r: r.phones), cap) | key_pairs(
        group(records, lambda r: r.houses), cap
    )


def channel_sparse(records, k=10, least=0.5, field="loose_n") -> set:
    """q-gram (bigram) TF-IDF cosine kNN over the name, through an inverted index."""
    from scipy.sparse import csr_matrix

    vocab, rows, cols, vals = {}, [], [], []
    texts = [getattr(r, field) + "|" + r.full_k for r in records]
    df = Counter(g for t in texts for g in bigrams(t))
    n = len(texts)
    for i, t in enumerate(texts):
        grams = bigrams(t) if len(t) > 2 else set()
        w = {g: math.log(n / df[g]) for g in grams}
        norm = math.sqrt(sum(v * v for v in w.values())) or 1
        for g, v in w.items():
            rows.append(i)
            cols.append(vocab.setdefault(g, len(vocab)))
            vals.append(v / norm)
    m = csr_matrix((vals, (rows, cols)), shape=(n, len(vocab)))
    out = set()
    mt = m.T.tocsr()
    for start in range(0, n, 2000):
        sims = (m[start : start + 2000] @ mt).tocsr()  # stays sparse: memory grows with matches
        for row in range(sims.shape[0]):
            i = start + row
            lo, hi = sims.indptr[row], sims.indptr[row + 1]
            cols, vals = sims.indices[lo:hi], sims.data[lo:hi]
            keep = (vals >= least) & (cols != i)
            cols, vals = cols[keep], vals[keep]
            if len(cols) > k:
                top = np.argpartition(-vals, k)[:k]
                cols = cols[top]
            for j in cols:
                out.add((min(i, int(j)), max(i, int(j))))
    return out


def channel_dense(vectors: np.ndarray, records, k=10, least=0.85, within_year=False) -> set:
    out = set()
    if within_year:
        blocks = defaultdict(list)
        for i, r in enumerate(records):
            blocks[r.dob[:4]].append(i)
        parts = [np.array(ids) for ids in blocks.values()]
    else:
        parts = [np.arange(len(records))]
    for ids in parts:
        v = vectors[ids]
        for start in range(0, len(ids), 2048):
            sims = v[start : start + 2048] @ v.T
            for row in range(sims.shape[0]):
                sims[row, start + row] = -1
            kk = min(k, len(ids) - 1)
            if kk <= 0:
                continue
            top = np.argpartition(-sims, kk, axis=1)[:, :kk]
            for row in range(sims.shape[0]):
                i = ids[start + row]
                for c in top[row]:
                    if sims[row, c] >= least:
                        j = ids[c]
                        out.add((min(i, j), max(i, j)))
    return out


# --- comparison features ----------------------------------------------------------------------------
FEATURES = [
    "jw_full_n", "jw_full_k", "jw_sur_n", "jw_giv_n", "eq_sur_k", "eq_giv_k", "eq_loose",
    "swap_name", "giv_match", "sur_match", "name_missing",
    "dob_exact", "dob_swapped", "dob_digits", "dob_year_diff",
    "gender", "phone_shared", "phone_min_freq", "phone_both",
    "house_shared", "unit_shared", "house_min_freq", "addr_best_jw", "same_locality", "addr_both",
    "rare_sur", "rare_giv", "rare_full",
    "dob_missing", "alias_match", "name_imputed", "dob_freq",
    "both_company", "type_mixed", "flag_agree", "core_jw_k", "core_jw_n", "core_eq",
]


def features(records, pairs, freq) -> np.ndarray:
    n = len(records)
    out = np.zeros((len(pairs), len(FEATURES)), dtype=np.float32)
    lf = lambda c, v: math.log(n / max(1, c.get(v, 1)))  # noqa: E731  rarity of a value
    for x, (i, j) in enumerate(pairs):
        a, b = records[i], records[j]
        giv = (a.giv_n and a.giv_n == b.giv_n) or (a.giv_k and a.giv_k == b.giv_k)
        sur = (a.sur_n and a.sur_n == b.sur_n) or (a.sur_k and a.sur_k == b.sur_k)
        swap = (a.sur_n and a.sur_n == b.giv_n and a.giv_n == b.sur_n) or (
            a.sur_k and a.sur_k == b.giv_k and a.giv_k == b.sur_k
        )
        both_dob = bool(a.dob) and bool(b.dob)
        dd = sum(p != q for p, q in zip(a.dob, b.dob, strict=False)) if both_dob and len(a.dob) == len(b.dob) else 10
        shared_ph = a.phones & b.phones
        shared_h = a.houses & b.houses
        best = max(
            (jaro_winkler(s, t) for s in a.addr_texts[:6] for t in b.addr_texts[:6]), default=0.0
        )
        gender = 0.0 if not a.gender or not b.gender else (1.0 if a.gender == b.gender else -1.0)
        out[x] = [
            jaro_winkler(a.full_n, b.full_n),
            jaro_winkler(a.full_k, b.full_k),
            jaro_winkler(a.sur_n, b.sur_n),
            jaro_winkler(a.giv_n, b.giv_n),
            float(bool(a.sur_k) and a.sur_k == b.sur_k),
            float(bool(a.giv_k) and a.giv_k == b.giv_k),
            float(bool(a.loose_n) and a.loose_n == b.loose_n),
            float(bool(swap)),
            float(bool(giv)),
            float(bool(sur)),
            float(not (a.full_n or a.full_k) or not (b.full_n or b.full_k)),
            float(both_dob and a.dob == b.dob),
            float(both_dob and swapped_md(a.dob) == b.dob),
            float(dd),
            abs(int(a.dob[:4]) - int(b.dob[:4])) if both_dob else 50.0,
            gender,
            float(len(shared_ph)),
            min((math.log(freq["phone"][p]) for p in shared_ph), default=-1.0),
            float(bool(a.phones) and bool(b.phones)),
            float(len(shared_h)),
            float(len(a.units & b.units)),
            min((math.log(freq["house"][h]) for h in shared_h), default=-1.0),
            best,
            float(bool(a.localities & b.localities)),
            float(bool(a.addr_texts) and bool(b.addr_texts)),
            lf(freq["sur_n"], a.sur_n) if a.sur_n and a.sur_n == b.sur_n else 0.0,
            lf(freq["giv_n"], a.giv_n) if a.giv_n and a.giv_n == b.giv_n else 0.0,
            lf(freq["full_n"], a.full_n) if a.full_n and a.full_n == b.full_n else 0.0,
            float(not both_dob),
            float(bool(a.aliases & ({b.sur_k} | b.aliases) - {""}) or bool(b.aliases & {a.sur_k} - {""})),
            float(a.imputed or b.imputed),
            math.log(freq["dob"].get(a.dob, 1)) if both_dob and a.dob == b.dob else -1.0,  # how many share it
            float(a.company_like and b.company_like),
            float((a.company_like and person_like(b)) or (b.company_like and person_like(a))),
            0.0 if not a.flag or not b.flag else (1.0 if a.flag == b.flag else -1.0),
            jaro_winkler(a.core_k, b.core_k),
            jaro_winkler(a.core_n, b.core_n),
            float((len(a.core_k) >= 2 and a.core_k == b.core_k) or (len(a.core_n) >= 3 and a.core_n == b.core_n)),
        ]
    return out


# --- scorers ----------------------------------------------------------------------------------------
def col(X, name):
    return X[:, FEATURES.index(name)]


def score_rules(X) -> np.ndarray:
    """Hand rules a careful analyst would write first."""
    dob = (col(X, "dob_exact") + col(X, "dob_swapped")) > 0
    name = (col(X, "eq_loose") > 0) | (col(X, "jw_full_k") >= 0.99) | (col(X, "jw_full_n") >= 0.95)
    married = (col(X, "giv_match") > 0) & (col(X, "phone_shared") + col(X, "house_shared") > 0)
    no_conflict = col(X, "gender") >= 0
    return ((dob & (name | married) & no_conflict)).astype(float)


LEVELS = {
    "name_n": lambda X: np.select(
        [col(X, "eq_loose") > 0, col(X, "jw_full_n") >= 0.92, col(X, "jw_full_n") >= 0.8, col(X, "jw_full_n") == 0],
        [0, 1, 2, 4], 3),
    "name_k": lambda X: np.select(
        [col(X, "jw_full_k") >= 0.99, col(X, "jw_full_k") >= 0.85, col(X, "jw_full_k") == 0], [0, 1, 3], 2),
    "given": lambda X: np.where(col(X, "giv_match") > 0, 0, np.where(col(X, "jw_giv_n") >= 0.85, 1, 2)),
    "dob": lambda X: np.select(
        [col(X, "dob_exact") > 0, col(X, "dob_swapped") > 0, col(X, "dob_digits") == 1], [0, 1, 2], 3),
    "gender": lambda X: np.select([col(X, "gender") > 0, col(X, "gender") < 0], [0, 1], 2),
    "phone": lambda X: np.select(
        [(col(X, "phone_shared") > 0) & (col(X, "phone_min_freq") < 1.5), col(X, "phone_shared") > 0, col(X, "phone_both") > 0],
        [0, 1, 2], 3),
    "address": lambda X: np.select(
        [col(X, "unit_shared") > 0, (col(X, "house_shared") > 0) & (col(X, "house_min_freq") < 1.5), col(X, "house_shared") > 0, col(X, "addr_best_jw") >= 0.9, col(X, "same_locality") > 0, col(X, "addr_both") > 0],
        [0, 1, 2, 3, 4, 5], 6),
}


def score_fs(X, tf=False, iters=40, prior=0.1, labelled=None, y=None):
    """Fellegi-Sunter by EM over the levels (conditional independence); with tf, an exact name
    agreement on a rare value weighs more (Splink-style term-frequency adjustment)."""
    L = {k: f(X).astype(int) for k, f in LEVELS.items()}
    sizes = {k: int(v.max()) + 1 for k, v in L.items()}
    m = {k: np.full(s, 1 / s) for k, s in sizes.items()}
    u = {k: np.bincount(L[k], minlength=sizes[k]) / len(X) for k in L}  # most candidates are non-matches
    for k in m:  # start m toward agreement
        m[k] = np.linspace(2, 0.2, sizes[k])
        m[k] /= m[k].sum()
    lam = prior
    for _ in range(iters):
        lm = np.log(lam) + sum(np.log(m[k][L[k]] + 1e-9) for k in L)
        lu = np.log(1 - lam) + sum(np.log(u[k][L[k]] + 1e-9) for k in L)
        g = 1 / (1 + np.exp(lu - lm))
        if labelled is not None:  # reviewed pairs are known (the studio's Learn weights)
            g[labelled] = y[labelled]
        lam = float(np.clip(g.mean(), 1e-4, 1 - 1e-4))
        for k in L:
            m[k] = np.bincount(L[k], weights=g, minlength=sizes[k]) + 1e-3
            m[k] /= m[k].sum()
            u[k] = np.bincount(L[k], weights=1 - g, minlength=sizes[k]) + 1e-3
            u[k] /= u[k].sum()
    lm = np.log(lam) + sum(np.log(m[k][L[k]]) for k in L)
    lu = np.log(1 - lam) + sum(np.log(u[k][L[k]]) for k in L)
    if tf:  # rarer exact names are stronger: replace u of the exact level by the value's share
        exact = L["name_n"] == 0
        base = math.log(u["name_n"][0])
        lu = lu + np.where(exact, -col(X, "rare_full") - base, 0)
    return 1 / (1 + np.exp(lu - lm))


def score_fs_random_u(X, Xr, iters=40, prior=0.1, tf=False):
    """Splink-style: u (how often levels agree by chance) from random record pairs; m (how often
    they agree for true matches) by EM over the candidates with u fixed."""
    L = {k: f(X).astype(int) for k, f in LEVELS.items()}
    Lr = {k: f(Xr).astype(int) for k, f in LEVELS.items()}
    sizes = {k: max(int(L[k].max()), int(Lr[k].max())) + 1 for k in L}
    u = {k: (np.bincount(Lr[k], minlength=sizes[k]) + 1.0) / (len(Xr) + sizes[k]) for k in L}
    m = {k: np.linspace(2, 0.2, sizes[k]) / np.linspace(2, 0.2, sizes[k]).sum() for k in L}
    lam = prior
    for _ in range(iters):
        lm = np.log(lam) + sum(np.log(m[k][L[k]]) for k in L)
        lu = np.log(1 - lam) + sum(np.log(u[k][L[k]]) for k in L)
        g = 1 / (1 + np.exp(lu - lm))
        lam = float(np.clip(g.mean(), 1e-4, 1 - 1e-4))
        for k in L:
            m[k] = np.bincount(L[k], weights=g, minlength=sizes[k]) + 1e-3
            m[k] /= m[k].sum()
    lm = np.log(lam) + sum(np.log(m[k][L[k]]) for k in L)
    lu = np.log(1 - lam) + sum(np.log(u[k][L[k]]) for k in L)
    if tf:
        exact = L["name_n"] == 0
        lu = lu + np.where(exact, -col(X, "rare_full") - math.log(u["name_n"][0]), 0)
    return 1 / (1 + np.exp(lu - lm))


def random_pairs(n, count=100_000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = rng.integers(0, n, count), rng.integers(0, n, count)
    keep = a != b
    return sorted({(int(min(x, y)), int(max(x, y))) for x, y in zip(a[keep], b[keep], strict=True)})


def active_labels(X, y, base_scores, budget, batch=50, seed=0, model="gbm"):
    """Simulated review: start from the most uncertain base scores, then retrain and ask about the
    pairs the model is least sure of. Returns final probabilities and the labelled indices."""
    rng = np.random.default_rng(seed)
    order = np.argsort(np.abs(base_scores - 0.5))
    labelled = list(order[: min(batch, budget)])
    # A few random pairs too, so the model sees both sides.
    labelled += list(rng.choice(len(X), size=min(len(X), batch), replace=False))
    labelled = list(dict.fromkeys(int(i) for i in labelled))
    prob = base_scores
    while True:
        prob = fit_predict(X, y, labelled, model, seed)
        if len(labelled) >= budget:
            break
        chosen = set(labelled)
        unsure = [int(i) for i in np.argsort(np.abs(prob - 0.5)) if int(i) not in chosen][:batch]
        if not unsure:
            break
        labelled += unsure
    prob = prob.copy()
    prob[labelled] = y[labelled]  # a reviewed pair is decided as reviewed
    return prob, labelled


def fit_predict(X, y, idx, model, seed=0):
    yl = y[idx]
    if yl.min() == yl.max():
        return np.full(len(X), float(yl[0]))
    if model == "lr":
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        sc = StandardScaler().fit(X[idx])
        clf = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(X[idx]), yl)
        return clf.predict_proba(sc.transform(X))[:, 1]
    import lightgbm as lgb

    clf = lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=5,
        subsample=0.9, colsample_bytree=0.9, random_state=seed, verbose=-1, n_jobs=1,
    ).fit(X[idx], yl)
    return clf.predict_proba(X)[:, 1]


# --- clustering ---------------------------------------------------------------------------------
class DSU:
    def __init__(self, n):
        self.p, self.size = list(range(n)), [1] * n

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            if self.size[a] < self.size[b]:
                a, b = b, a
            self.p[b] = a
            self.size[a] += self.size[b]


def cluster_cc(n, pairs, prob, t=0.5):
    d = DSU(n)
    for (i, j), p in zip(pairs, prob, strict=True):
        if p >= t:
            d.union(i, j)
    return [d.find(i) for i in range(n)]


def cluster_guarded(n, pairs, prob, t=0.5, low=0.1, cap=12, never=None):
    """Strongest edges first; refuse a merge that would join two records decided different
    (cannot-link: scored below `low`), or any pair `never` says must stay apart (e.g. a company and
    a person), or grow a cluster past `cap`."""
    d = DSU(n)
    members = {i: {i} for i in range(n)}
    cannot = defaultdict(set)
    for (i, j), p in zip(pairs, prob, strict=True):
        if p < low or (never and never(i, j)):
            cannot[i].add(j)
            cannot[j].add(i)
    for k in np.argsort(-np.asarray(prob)):
        if prob[k] < t:
            break
        i, j = pairs[k]
        a, b = d.find(i), d.find(j)
        if a == b:
            continue
        ma, mb = members[a], members[b]
        if len(ma) + len(mb) > cap or any(cannot[x] & mb for x in ma):
            continue
        d.union(a, b)
        root = d.find(a)
        members[root] = ma | mb
    return [d.find(i) for i in range(n)]


def cluster_pivot(n, pairs, prob, t=0.5, seed=0):
    """KwikCluster (a 3-approximation of correlation clustering) on edges at or above t."""
    adj = defaultdict(set)
    for (i, j), p in zip(pairs, prob, strict=True):
        if p >= t:
            adj[i].add(j)
            adj[j].add(i)
    order = list(range(n))
    np.random.default_rng(seed).shuffle(order)
    label, done = [None] * n, set()
    for v in order:
        if v in done:
            continue
        group = {v} | (adj[v] - done)
        for u in group:
            label[u] = v
        done |= group
    return label


# --- metrics --------------------------------------------------------------------------------------
def evaluate(labels, records, key, tags, households, truth_pairs, entity_of=None):
    n = len(records)
    ent = [key[r.cont_id] for r in records]
    by_pred = defaultdict(list)
    for i, lab in enumerate(labels):
        by_pred[lab].append(i)
    pred_pairs = {(a, b) for ids in by_pred.values() for a, b in combinations(sorted(ids), 2)}
    tp = len(pred_pairs & truth_pairs)
    p = tp / len(pred_pairs) if pred_pairs else 1.0
    r = tp / len(truth_pairs) if truth_pairs else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    # B-cubed
    by_true = defaultdict(set)
    for i, e in enumerate(ent):
        by_true[e].add(i)
    bp = br = 0.0
    for ids in by_pred.values():
        s = set(ids)
        for i in ids:
            inter = len(s & by_true[ent[i]])
            bp += inter / len(s)
            br += inter / len(by_true[ent[i]])
    bp, br = bp / n, br / n
    bf = 2 * bp * br / (bp + br) if bp + br else 0.0
    # recall per dirt tag (true pairs touching a contact with that tag)
    per_tag = Counter()
    per_tag_found = Counter()
    for a, b in truth_pairs:
        for t in tags[records[a].cont_id] | tags[records[b].cont_id]:
            per_tag[t] += 1
            per_tag_found[t] += (a, b) in pred_pairs
    false = Counter()
    for a, b in pred_pairs - truth_pairs:
        ea, eb = ent[a], ent[b]
        if households.get(ea) == households.get(eb):
            false["same_household"] += 1
        elif "lookalike" in tags[records[a].cont_id] and "lookalike" in tags[records[b].cont_id]:
            false["lookalike"] += 1
        else:
            false["other"] += 1
    sizes = Counter(len(v) for v in by_pred.values())
    return {
        "pairwise": {"p": round(p, 4), "r": round(r, 4), "f1": round(f, 4)},
        "bcubed": {"p": round(bp, 4), "r": round(br, 4), "f1": round(bf, 4)},
        "largest": max(len(v) for v in by_pred.values()),
        "clusters": len(by_pred),
        "false_merges": dict(false),
        "recall_by_tag": {
            t: round(per_tag_found[t] / per_tag[t], 3)
            for t, c in per_tag.most_common()
            if c >= 20
        },
        "sizes": dict(sorted(sizes.items())[:8]),
    }


def by_type(labels, records, key, tags):
    """Pairwise confusion by entity type: people, companies, wrong person-company merges, and
    sole proprietors' shop contacts joined to their owner."""
    ent = [key[r.cont_id] for r in records]
    company = [e.startswith("C") for e in ent]
    by_pred, by_true = defaultdict(list), defaultdict(list)
    for i, lab in enumerate(labels):
        by_pred[lab].append(i)
    for i, e in enumerate(ent):
        by_true[e].append(i)
    pred = {(a, b) for ids in by_pred.values() for a, b in combinations(sorted(ids), 2)}
    truth = {(a, b) for ids in by_true.values() for a, b in combinations(sorted(ids), 2)}
    out = {}
    for name, keep in (("people", lambda a, b: not company[a] and not company[b]),
                       ("companies", lambda a, b: company[a] and company[b])):
        t = {p for p in truth if keep(*p)}
        f = {p for p in pred if keep(*p)}
        tp = len(t & f)
        out[name] = {"TP": tp, "FP": len(f - t), "FN": len(t - f),
                     "f1": round(2 * tp / (len(t) + len(f)), 4) if t or f else 1.0}
    out["person_company_merges"] = sum(1 for a, b in pred if company[a] != company[b])
    shop = [i for i, r in enumerate(records) if "sole_proprietor_shop" in tags[r.cont_id]]
    links = [(min(i, j), max(i, j)) for i in shop for j in by_true[ent[i]] if j != i and "sole_proprietor_shop" not in tags[records[j].cont_id]]
    out["shop_to_owner"] = {"pairs": len(links), "found": sum(1 for p in links if p in pred)}
    return out


def candidate_report(found: set, truth: set) -> dict:
    hit = len(found & truth)
    return {"pairs": len(found), "recall": round(hit / len(truth), 4), "true_found": hit}


def timed(f, *a, **k):
    t = time.time()
    out = f(*a, **k)
    return out, round(time.time() - t, 2)


__all__ = [
    "frequencies",
    "channel_exact",
    "channel_sets",
    "channel_sparse",
    "channel_dense",
    "features",
    "score_rules",
    "score_fs",
    "active_labels",
    "cluster_cc",
    "cluster_guarded",
    "cluster_pivot",
    "evaluate",
    "candidate_report",
    "timed",
]
