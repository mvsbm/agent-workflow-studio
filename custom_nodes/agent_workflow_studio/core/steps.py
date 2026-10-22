"""The contact-matching steps, one function per node: `step(ctx, **inputs) -> outputs` (a tuple in
the node's output order). Datasets are file paths; each step writes its files in ctx.run_dir and
tells what it did through ctx.report / ctx.facts. The studio's nodes and an exported CLI both call
these, so they compute the same thing.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import candidates as K
from . import contacts as C
from . import features as F
from . import learn as L
from .clusters import guard_clusters, stable_ids
from .context import Context
from .decide import COLUMNS, decide_by_score


def write_csv(path: Path, rows, columns: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def records_of(path) -> list[C.Record]:
    return [C.record_of(r) for r in C.load_jsonl(Path(path))]


# --- 1 read --------------------------------------------------------------------------------------
def read_contacts(ctx: Context, folder: str, columns: str) -> tuple:
    raw, stats = C.read_contacts(ctx, folder, columns)
    path = ctx.run_dir / "contacts.jsonl"
    C.save_jsonl(path, raw)
    with_phone = sum(1 for r in raw if r["phones"])
    with_address = sum(1 for r in raw if r["addresses"])
    ctx.facts.update(
        {
            "contacts": stats["contacts"],
            "duplicate keys": stats.get("duplicate keys", 0),
            "rows without a key": stats.get("rows without a key", 0),
            "birth dates unreadable": stats.get("birth dates unreadable", 0),
            "with a phone": with_phone,
            "with an address": with_address,
            "phones without a contact": stats.get("phones without a contact", 0),
            "addresses without a contact": stats.get("addresses without a contact", 0),
        }
    )
    ctx.report(
        f"READ CONTACTS · {stats['contacts']:,} contacts · "
        f"{stats.get('duplicate keys', 0)} duplicate keys · "
        f"{stats.get('birth dates unreadable', 0)} birth dates unreadable · "
        f"{with_phone:,} with a phone · {with_address:,} with an address"
    )
    return (path,)


# --- 2 standardize -------------------------------------------------------------------------------
def standardize_contacts(
    ctx: Context,
    contacts,
    towns: str,
    kana_ocr: bool,
    readings: bool,
    split_one_field: bool,
    shop_alias: bool,
    company_clean: bool,
) -> tuple:
    chosen = dict(
        kana_ocr=kana_ocr,
        readings=readings,
        split_one_field=split_one_field,
        shop_alias=shop_alias,
        company_clean=company_clean,
    )
    registry = C.load_towns(Path(ctx.input_dir) / towns) if towns else {}
    recs = C.standardize(
        C.load_jsonl(Path(contacts)), registry, [k for k, on in chosen.items() if on]
    )
    path = ctx.run_dir / "records.jsonl"
    C.save_jsonl(path, (C.record_row(r) for r in recs))
    n = max(1, len(recs))
    named = sum(1 for r in recs if r.full_n or r.full_k)
    ctx.facts.update(
        {
            "records": len(recs),
            "with a usable name": f"{named / n:.1%}",
            "with kana": f"{sum(1 for r in recs if r.full_n) / n:.1%}",
            "kana filled from readings": sum(1 for r in recs if r.imputed),
            "look like a company": sum(1 for r in recs if r.company_like or r.flag == "2"),
            "look like a shop": sum(1 for r in recs if r.shop_like),
            "towns in the registry": len(registry),
            "addresses parsed to a house": sum(1 for r in recs if r.houses),
        }
    )
    ctx.report(
        f"STANDARDIZE · {len(recs):,} records · {named / n:.1%} with a usable name · "
        f"{sum(1 for r in recs if r.imputed):,} kana filled from readings"
    )
    return (path,)


# --- 3 candidates --------------------------------------------------------------------------------
def find_contact_candidates(
    ctx: Context,
    records,
    exact: bool,
    shared: bool,
    locality: bool,
    similar: bool,
    exact_cap: int,
    shared_cap: int,
    similarity: float,
) -> tuple:
    recs = records_of(records)
    passes = [
        p
        for p, on in (
            ("exact", exact),
            ("sets", shared),
            ("locality", locality),
            ("similar", similar),
        )
        if on
    ]
    found = {}
    if "exact" in passes:
        found["exact"] = K.channel_exact(recs, exact_cap)
    if "sets" in passes:
        found["shared phone or house"] = K.channel_sets(recs, shared_cap)
    if "locality" in passes:
        found["birth date + town"] = K.channel_locality(recs, shared_cap)
    if "similar" in passes:
        found["similar names"] = K.channel_similar(recs, least=similarity)
    pairs = sorted(set().union(*found.values())) if found else []
    path = ctx.run_dir / "candidates.csv"
    write_csv(
        path,
        (
            {
                "left": recs[i].key,
                "right": recs[j].key,
                "found_by": "+".join(name for name, p in found.items() if (i, j) in p),
            }
            for i, j in pairs
        ),
        ["left", "right", "found_by"],
    )
    ctx.facts.update({"pairs": len(pairs), **{f"found by {k}": len(v) for k, v in found.items()}})
    ctx.facts["pairs per record"] = round(2 * len(pairs) / max(1, len(recs)), 2)
    ctx.report(f"CANDIDATES · {len(pairs):,} pairs · {ctx.facts['pairs per record']} per record")
    return (path,)


# --- 4 compare -----------------------------------------------------------------------------------
def compare_contacts(ctx: Context, records, candidates) -> tuple:
    recs = records_of(records)
    index = {r.key: i for i, r in enumerate(recs)}
    rows = read_csv(candidates)
    pairs = [(index[r["left"]], index[r["right"]]) for r in rows]
    X = F.features(recs, pairs, C.frequencies(recs))
    path = ctx.run_dir / "pairs.jsonl"
    # shortcut: features as JSON lines, fine at 10k-100k contacts; upgrade to .npy if 1M is slow.
    C.save_jsonl(
        path,
        (
            {
                "id": f"{r['left']}|{r['right']}",
                "left": r["left"],
                "right": r["right"],
                "found_by": r["found_by"],
                "features": [float(v) for v in x],
            }
            for r, x in zip(rows, X, strict=True)
        ),
    )
    (ctx.run_dir / "meta.json").write_text(
        json.dumps({"records": str(Path(records).resolve()), "features": F.FEATURES}),
        encoding="utf-8",
    )
    same_dob = int((F.col(X, "dob_exact") > 0).sum()) if len(X) else 0
    ctx.facts.update(
        {
            "pairs compared": len(rows),
            "measures per pair": len(F.FEATURES),
            "same birth date": same_dob,
            "share a phone": int((F.col(X, "phone_shared") > 0).sum()) if len(X) else 0,
            "share a house": int((F.col(X, "house_shared") > 0).sum()) if len(X) else 0,
        }
    )
    ctx.report(f"COMPARE · {len(rows):,} pairs · {len(F.FEATURES)} measures each")
    return (path,)


# --- 5 learn -------------------------------------------------------------------------------------
def shown(r: C.Record) -> dict:
    """A record as the Review tab shows it."""
    return {
        "name": " ".join(x for x in (r.sur_k, r.giv_k) if x),
        "kana": " ".join(x for x in (r.sur_n, r.giv_n) if x),
        "birth": r.dob,
        "gender": r.gender,
        "type": {"1": "person", "2": "company"}.get(r.flag, r.flag),
        "phones": ", ".join(sorted(r.phones))[:80],
        "address": " / ".join(r.addr_texts[:2])[:120],
        "id": r.key,
    }


def answer_key(path: Path) -> dict[str, str]:
    """record_id -> entity_id (a benchmark's answer key)."""
    return {r["record_id"]: r["entity_id"] for r in read_csv(path)}


def learn_from_reviews(
    ctx: Context,
    pairs,
    batch: int,
    audit: bool,
    answer_key_file: str = "",
    budget: int = 1000,
    seed: int = 0,
) -> tuple:
    rows = C.load_jsonl(Path(pairs))
    meta = json.loads((Path(pairs).parent / "meta.json").read_text(encoding="utf-8"))
    X = np.array([r["features"] for r in rows], dtype=np.float32).reshape(
        len(rows), len(meta["features"])
    )
    ids = [r["id"] for r in rows]
    base = L.day_zero(X)
    asked_again, again = set(), []
    for old in sorted(Path(ctx.run_dir).parent.glob("learn_from_reviews-*/asked_again.json")):
        if old.parent != Path(ctx.run_dir):
            asked_again |= set(json.loads(old.read_text(encoding="utf-8")))
    if answer_key_file:  # benchmark: a reviewer who knows the answers
        key = answer_key(Path(answer_key_file))
        y = np.array([key.get(r["left"]) == key.get(r["right"]) for r in rows], dtype=float)
        prob, labelled = L.simulate(X, y, base, budget, batch, seed)
        labels = {ids[k]: "same" if y[k] else "different" for k in labelled}
        ask = []
        how = f"simulated reviewer: {len(labelled):,} reviews from the answer key"
    else:
        labels = {i: ctx.labels[i] for i in ids if ctx.labels.get(i) in ("same", "different")}
        labelled = [k for k, i in enumerate(ids) if i in labels]
        y = np.array([labels.get(i) == "same" for i in ids], dtype=float)
        if not labelled:
            prob, ask = base, L.first_batch(base, batch, seed)
            how = "no reviews yet: scored by agreement patterns (day 0)"
        else:
            prob = L.fit(X, y, labelled).copy()
            prob[labelled] = y[labelled]
            ask = L.unsure_first(prob, set(labelled), batch)
            if audit:
                again = [k for k in L.disputed(X, y, labelled) if ids[k] not in asked_again]
            how = f"learned from {len(labelled):,} of your reviews"
    corroborated = F.corroborated(X) if len(X) else np.zeros(0, bool)
    path = ctx.run_dir / "scores.jsonl"
    C.save_jsonl(
        path,
        (
            {
                "id": ids[k],
                "left": r["left"],
                "right": r["right"],
                "score": round(float(prob[k]), 6),
                "label": labels.get(ids[k], ""),
                "corroborated": int(corroborated[k]),
            }
            for k, r in enumerate(rows)
        ),
    )
    (ctx.run_dir / "asked_again.json").write_text(
        json.dumps(sorted(asked_again | {ids[k] for k in again})), encoding="utf-8"
    )
    recs = {r.key: r for r in records_of(meta["records"])} if (ask or again) else {}
    review = {
        "stage": ctx.stage or {"title": "Learn from your reviews"},
        "title": f"Learn from your reviews · {len(ask)} pairs the model is least sure of"
        + (f" · {len(again)} reviews it disputes (review again)" if again else ""),
        "pairs": [
            {
                "id": ids[k],
                "a": shown(recs[rows[k]["left"]]),
                "b": shown(recs[rows[k]["right"]]),
                "score": round(float(prob[k]), 2),
                "branch": "Review again" if k in again else "Least sure",
                "reason": (
                    f"you said {labels.get(ids[k])}; a model trained without this review strongly disagrees"
                    if k in again
                    else f"{float(prob[k]):.0%} likely the same"
                ),
            }
            for k in [*again, *ask]
        ],
    }
    (ctx.run_dir / "review.json").write_text(
        json.dumps(review, ensure_ascii=False), encoding="utf-8"
    )
    same = sum(1 for v in labels.values() if v == "same")
    ctx.facts.update(
        {
            "pairs scored": len(rows),
            "how": how,
            "reviewed: same": same,
            "reviewed: different": len(labels) - same,
            "likely the same (≥ 50%)": int((prob >= 0.5).sum()) if len(rows) else 0,
            "next to review": len(ask),
            "reviews to check again": len(again),
        }
    )
    summary = (
        f"LEARN · {len(rows):,} pairs · {how} · {int((prob >= 0.5).sum()) if len(rows) else 0:,} likely the same"
        f" · next: review {len(ask)} pairs"
        + (f" and {len(again)} disputed reviews" if again else "")
    )
    ctx.report(summary)
    return path, summary


# --- 6 decide ------------------------------------------------------------------------------------
def decide_pairs(
    ctx: Context, pairs, match_at: float, review_from: float, corroboration: bool
) -> tuple:
    decided = decide_by_score(C.load_jsonl(Path(pairs)), match_at, review_from, corroboration)
    path, queue = ctx.run_dir / "decisions.csv", ctx.run_dir / "review_queue.csv"
    write_csv(path, decided, COLUMNS)
    write_csv(queue, (d for d in decided if d["decision"] == "review"), COLUMNS)
    counts = Counter(d["decision"] for d in decided)
    ctx.extra["counts"] = counts
    branches = Counter((d["branch"], d["decision"]) for d in decided)
    ctx.facts.update({f"{b} → {d}": n for (b, d), n in sorted(branches.items())})
    summary = (
        f"DECIDE · {len(decided):,} pairs · {counts['match']:,} match · {counts['review']:,} to review"
        f" · {counts['different']:,} different"
    )
    ctx.report(summary)
    return path, queue, summary


# --- 7 group -------------------------------------------------------------------------------------
def cluster_guard(
    ctx: Context, data=None, max_size: int = 60, scores_separate: bool = False, **branches
) -> tuple:
    """`data`: all decisions; `branches`: stage name -> a branch's decisions (several stages)."""
    decisions = read_csv(data) if data else []
    for name, path in sorted(branches.items()):
        if path:
            decisions += [{**row, "stage": name} for row in read_csv(path)]
    if not decisions:
        raise ValueError(
            "Connect the decisions, or the matched / different branches of each stage."
        )
    guarded = guard_clusters(decisions, max_size, scores_separate)
    earlier = sorted(
        f
        for f in Path(ctx.run_dir).parent.glob("cluster_guard-*/clusters.csv")
        if f.parent != Path(ctx.run_dir)
    )
    previous = (
        {
            r.get("record", r.get("mention")): r.get("cluster", r.get("customer"))
            for r in read_csv(earlier[-1])
        }
        if earlier
        else {}
    )
    ids, since = stable_ids(guarded["clusters"], previous)
    path = ctx.run_dir / "clusters.csv"
    write_csv(
        path,
        [{"record": m, "cluster": ids[r]} for m, r in sorted(guarded["clusters"].items())],
        ["record", "cluster"],
    )
    write_csv(
        ctx.run_dir / "blocked.csv",
        guarded["blocked"],
        ["id", "left", "right", "branch", "score", "reason"],
    )
    ctx.extra.update(guarded=guarded, ids=ids, since=since, previous=previous, decisions=decisions)
    sizes = Counter(guarded["clusters"].values())
    ctx.report(
        f"CLUSTER GUARD · {len(sizes):,} clusters · largest {max(sizes.values(), default=0)} records · "
        f"{len(guarded['merged']):,} merges accepted · {len(guarded['blocked']):,} blocked"
    )
    return (path,)


# --- 8 publish -----------------------------------------------------------------------------------
def entity_per_contact(ctx: Context, records, clusters) -> tuple:
    """Every contact with its entity id: the lowest key of the contacts grouped with it (its
    own key when alone), the same however often it runs."""
    members = defaultdict(list)
    for r in read_csv(clusters):
        members[r["cluster"]].append(r["record"])
    lowest = lambda keys: min(keys, key=lambda k: (len(k), k))  # noqa: E731  numeric order for digit keys
    entity = {key: lowest(keys) for keys in members.values() for key in keys}
    rows = [
        {"record": key, "cluster": entity.get(key, key)}
        for key in (r["key"] for r in C.load_jsonl(Path(records)))
    ]
    path = ctx.run_dir / "entities.csv"
    write_csv(path, rows, ["record", "cluster"])
    entities = len({r["cluster"] for r in rows})
    ctx.facts.update(
        {"contacts": len(rows), "entities": entities, "contacts merged away": len(rows) - entities}
    )
    ctx.report(f"ENTITIES · {len(rows):,} contacts → {entities:,} entities")
    return (path,)
