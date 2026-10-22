"""How alike two contacts are, as numbers (research/contacts_er/bench.py `features`): names in both
scripts, birth date (and how many share it), phones and houses (and how common they are),
addresses, the company name core."""

from __future__ import annotations

import math

import numpy as np

from .candidates import swapped_md

FEATURES = [
    "jw_full_n", "jw_full_k", "jw_sur_n", "jw_giv_n", "eq_sur_k", "eq_giv_k", "eq_loose",
    "swap_name", "giv_match", "sur_match", "name_missing",
    "dob_exact", "dob_swapped", "dob_digits", "dob_year_diff",
    "gender", "phone_shared", "phone_min_freq", "phone_both",
    "house_shared", "unit_shared", "house_min_freq", "addr_best_jw", "same_locality", "addr_both",
    "rare_sur", "rare_giv", "rare_full",
    "dob_missing", "alias_match", "name_imputed", "dob_freq",
    "both_company", "type_mixed", "flag_agree", "core_jw_k", "core_jw_n", "core_eq",
]  # fmt: skip


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


def person_like(r) -> bool:
    return bool(r.giv_k or r.giv_n) and not r.company_like and not r.shop_like


def year(dob: str) -> int:
    return int(dob[:4]) if dob[:4].isdigit() else 0


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
        dd = (
            sum(p != q for p, q in zip(a.dob, b.dob, strict=False))
            if both_dob and len(a.dob) == len(b.dob)
            else 10
        )
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
            float(bool(a.loose_n) and bool({a.loose_n, a.loose_v} & {b.loose_n, b.loose_v} - {""})),
            float(bool(swap)),
            float(bool(giv)),
            float(bool(sur)),
            float(not (a.full_n or a.full_k) or not (b.full_n or b.full_k)),
            float(both_dob and a.dob == b.dob),
            float(both_dob and swapped_md(a.dob) == b.dob),
            float(dd),
            min(abs(year(a.dob) - year(b.dob)), 50)
            if both_dob
            else 50.0,  # a year typo (1067): no outlier
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
            float(
                bool(a.aliases & ({b.sur_k} | b.aliases) - {""})
                or bool(b.aliases & {a.sur_k} - {""})
            ),
            float(a.imputed or b.imputed),
            math.log(freq["dob"].get(a.dob, 1)) if both_dob and a.dob == b.dob else -1.0,
            float(a.company_like and b.company_like),
            float((a.company_like and person_like(b)) or (b.company_like and person_like(a))),
            0.0 if not a.flag or not b.flag else (1.0 if a.flag == b.flag else -1.0),
            jaro_winkler(a.core_k, b.core_k),
            jaro_winkler(a.core_n, b.core_n),
            float(
                (len(a.core_k) >= 2 and a.core_k == b.core_k)
                or (len(a.core_n) >= 3 and a.core_n == b.core_n)
            ),
        ]
    return out


def col(X, name):
    return X[:, FEATURES.index(name)]


def corroborated(X) -> np.ndarray:
    """A second identifier beside name and birth date: shared phone, house, unit, town or a
    stated alias (the precision-first rule)."""
    return (
        (col(X, "phone_shared") > 0)
        | (col(X, "house_shared") > 0)
        | (col(X, "unit_shared") > 0)
        | (col(X, "same_locality") > 0)
        | (col(X, "alias_match") > 0)
    )
