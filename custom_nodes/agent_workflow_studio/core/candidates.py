"""Candidate pairs of contacts (research/contacts_er/bench.py channels): exact keys, shared phones
and houses, birth date + locality, and name similarity (q-gram TF-IDF). Groups larger than a cap
are skipped: a common key pairs no one."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from itertools import combinations

import numpy as np

PASSES = ("exact", "sets", "locality", "similar")


def key_pairs(groups: dict, cap: int) -> set:
    out = set()
    for ids in groups.values():
        if 1 < len(ids) <= cap:
            out.update(combinations(sorted(ids), 2))
    return out


def group(records, keyf) -> dict:
    g = defaultdict(set)
    for i, r in enumerate(records):
        for k in keyf(r):
            if k:
                g[k].add(i)
    return g


def swapped_md(dob: str) -> str:
    y, m, d = (dob.split("-") + ["", ""])[:3]
    return f"{y}-{d}-{m}" if m and d and m != d else ""


def bigrams(s: str) -> set:
    s = f"^{s}$"
    return {s[i : i + 2] for i in range(len(s) - 1)}


def channel_exact(records, cap=50) -> set:
    """Birth date with each name part (kana and kanji), and the full name alone (a typo in the
    birth date still meets); a contact with no birth date meets every contact with its full name."""
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
    for field in ("full_n", "full_k"):
        names = group(
            records, lambda r, f=field: [getattr(r, f)] if len(getattr(r, f)) >= 3 else []
        )
        for ids in names.values():
            unknown = [i for i in ids if not records[i].dob]
            if unknown and len(ids) <= 200:
                out.update((min(i, j), max(i, j)) for i in unknown for j in ids if i != j)
    for f in keys:
        g = group(records, f)
        if f is keys[6]:  # the swapped date meets the other contact's plain date key
            g2 = group(records, keys[0])
            for k, ids in g.items():
                if k in g2:
                    both = ids | g2[k]
                    if len(both) <= cap:
                        out.update(combinations(sorted(both), 2))
            continue
        out |= key_pairs(g, cap)
    return out


def channel_sets(records, cap=20) -> set:
    """Shared phone, shared house: a family shares them too, so they only make candidates."""
    return key_pairs(group(records, lambda r: r.phones), cap) | key_pairs(
        group(records, lambda r: r.houses), cap
    )


def channel_locality(records, cap=20) -> set:
    """Birth date + a shared town: a contact with no name meets the same person."""
    return key_pairs(
        group(records, lambda r: [r.dob + "|" + loc for loc in r.localities] if r.dob else []), cap
    )


def channel_similar(records, k=10, least=0.5) -> set:
    """Names alike in their 2-character pieces (TF-IDF cosine, k nearest above `least`)."""
    from scipy.sparse import csr_matrix

    vocab, rows, cols, vals = {}, [], [], []
    texts = [r.loose_n + "|" + r.full_k for r in records]
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
            found, score = sims.indices[lo:hi], sims.data[lo:hi]
            keep = (score >= least) & (found != i)
            found, score = found[keep], score[keep]
            if len(found) > k:
                found = found[np.argpartition(-score, k)[:k]]
            for j in found:
                out.add((min(i, int(j)), max(i, int(j))))
    return out


def find_pairs(records, passes=PASSES, exact_cap=50, set_cap=20, similar=0.5) -> tuple[list, dict]:
    """The candidate pairs (record index pairs, sorted) and how many each pass found."""
    found = {}
    if "exact" in passes:
        found["exact"] = channel_exact(records, exact_cap)
    if "sets" in passes:
        found["sets"] = channel_sets(records, set_cap)
    if "locality" in passes:
        found["locality"] = channel_locality(records, set_cap)
    if "similar" in passes:
        found["similar"] = channel_similar(records, least=similar)
    pairs = sorted(set().union(*found.values())) if found else []
    return pairs, {name: len(p) for name, p in found.items()}
