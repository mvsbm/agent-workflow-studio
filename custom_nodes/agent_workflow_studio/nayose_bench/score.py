"""Score a nayose result against the hidden answer key.

The result is a CSV with columns system, record_id, role, customer_id: one row per person
mention. Mentions it leaves out count as customers of their own. Reports pairwise and B-cubed
precision/recall/F1, recall per damage type and per system pair, and what the false merges were
(same household, same name, look-alike strangers).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

KEY_COLUMNS = ("system", "record_id", "role")


def f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def load_result(path: Path) -> tuple[dict, list[str]]:
    problems = []
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    missing = {*KEY_COLUMNS, "customer_id"} - set(reader.fieldnames or [])
    if missing:
        return {}, [f"result lacks columns: {', '.join(sorted(missing))}"]
    result = {}
    for row in reader:
        mention = tuple((row[c] or "").strip() for c in KEY_COLUMNS)
        if mention in result and result[mention] != row["customer_id"]:
            problems.append(f"conflicting customer ids for {mention}")
        result[mention] = (row["customer_id"] or "").strip()
    return result, problems


def score(key_dir: Path, result_path: Path) -> dict:
    key = {}
    for part in sorted(key_dir.glob("answer_key*.csv")):  # one file per exported system
        with open(part, encoding="utf-8") as f:
            key |= {
                tuple(r[c] for c in KEY_COLUMNS): (
                    r["entity_id"],
                    set(filter(None, r["tags"].split(";"))),
                )
                for r in csv.DictReader(f)
            }
    with open(key_dir / "entities.csv", encoding="utf-8") as f:
        entities = {r["entity_id"]: r for r in csv.DictReader(f)}
    result, problems = load_result(result_path)
    pred = {m: ("id", result[m]) if result.get(m) else ("alone", m) for m in key}
    true = {m: key[m][0] for m in key}

    pairs = Counter((pred[m], true[m]) for m in key)
    pred_sizes, true_sizes = Counter(pred.values()), Counter(true.values())
    c2 = lambda n: n * (n - 1) // 2  # noqa: E731
    tp = sum(c2(n) for n in pairs.values())
    predicted, actual = (
        sum(c2(n) for n in pred_sizes.values()),
        sum(c2(n) for n in true_sizes.values()),
    )
    pw_p, pw_r = (tp / predicted if predicted else 1.0), (tp / actual if actual else 1.0)
    b_p = sum(pairs[(pred[m], true[m])] / pred_sizes[pred[m]] for m in key) / len(key)
    b_r = sum(pairs[(pred[m], true[m])] / true_sizes[true[m]] for m in key) / len(key)

    # Recall of true pairs, by the damage on either side and by the systems involved.
    by_tag, by_systems = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    members = defaultdict(list)
    for m in key:
        members[true[m]].append(m)
    for group in members.values():
        for a, b in combinations(group, 2):
            hit = pred[a] == pred[b]
            for tag in key[a][1] | key[b][1]:
                by_tag[tag][0] += hit
                by_tag[tag][1] += 1
            systems = " + ".join(sorted({a[0], b[0]})) if a[0] != b[0] else f"within {a[0]}"
            by_systems[systems][0] += hit
            by_systems[systems][1] += 1

    # What the false merges were.
    false_merges, giant = Counter(), 0
    clusters = defaultdict(list)
    for m in key:
        clusters[pred[m]].append(m)
    for group in clusters.values():
        if len(group) > 300:
            giant += 1
            continue
        for a, b in combinations(group, 2):
            ea, eb = entities[true[a]], entities[true[b]]
            if ea is eb:
                continue
            kind = (
                "same household"
                if ea["household"] == eb["household"] and ea["household"]
                else "same name and birth date"
                if ea["name_kana"] == eb["name_kana"] and ea["dob"] == eb["dob"]
                else "same name"
                if ea["name_kana"] == eb["name_kana"]
                else "other"
            )
            false_merges[kind] += 1

    rate = lambda hit_total: round(hit_total[0] / hit_total[1], 3) if hit_total[1] else None  # noqa: E731
    return {
        "mentions": len(key),
        "covered": round(sum(m in result for m in key) / len(key), 3),
        "unknown_rows": sum(m not in key for m in result),
        "problems": problems[:10],
        "pairwise": {
            "precision": round(pw_p, 3),
            "recall": round(pw_r, 3),
            "f1": round(f1(pw_p, pw_r), 3),
        },
        "bcubed": {
            "precision": round(b_p, 3),
            "recall": round(b_r, 3),
            "f1": round(f1(b_p, b_r), 3),
        },
        "customers": {"predicted": len(pred_sizes), "true": len(true_sizes)},
        "recall_by_damage": {
            t: rate(v) for t, v in sorted(by_tag.items(), key=lambda kv: -kv[1][1])
        },
        "recall_by_systems": {s: rate(v) for s, v in sorted(by_systems.items())},
        "false_merges": dict(false_merges.most_common()),
        "giant_clusters": giant,
    }


def funnel(key_dir: Path, result_path: Path, candidates: list[tuple[str, str]]) -> dict:
    """Where the true matches are lost: never compared as a pair (retrieval), or compared but not
    merged (decisions, review, guard). Mentions are "system/record_id/role", as in the pairs."""
    key = {}
    for part in sorted(key_dir.glob("answer_key*.csv")):
        with open(part, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key[f"{r['system']}/{r['record_id']}/{r['role']}"] = (
                    r["entity_id"],
                    set(filter(None, r["tags"].split(";"))),
                )
    result, _ = load_result(result_path)
    customer = {"/".join(m): c for m, c in result.items() if c}
    compared = {frozenset(pair) for pair in candidates}
    members = defaultdict(list)
    for m, (entity, _) in key.items():
        members[entity].append(m)
    counts, missed = Counter(), Counter()
    for group in members.values():
        for a, b in combinations(group, 2):
            counts["true pairs"] += 1
            if customer.get(a) and customer.get(a) == customer.get(b):
                # found directly, or joined through others (a = c = b)
                counts["same customer"] += 1
            elif frozenset((a, b)) not in compared:
                counts["never a candidate"] += 1
                missed.update(key[a][1] | key[b][1] or {"clean"})
            else:
                counts["candidate, not merged"] += 1
    return {"counts": dict(counts), "never a candidate, by damage": dict(missed.most_common(6))}


def text(report: dict) -> str:
    pw, b = report["pairwise"], report["bcubed"]
    worst = sorted(((v, t) for t, v in report["recall_by_damage"].items() if v is not None))[:6]
    return "\n".join(
        [
            f"pairwise P {pw['precision']} R {pw['recall']} F1 {pw['f1']} · B-cubed F1 {b['f1']} · "
            f"covered {report['covered']} · customers {report['customers']['predicted']} (true {report['customers']['true']})",
            "hardest damage: " + ", ".join(f"{t} {v}" for v, t in worst),
            "by systems: " + ", ".join(f"{s} {v}" for s, v in report["recall_by_systems"].items()),
            "false merges: "
            + (", ".join(f"{k} {v}" for k, v in report["false_merges"].items()) or "none"),
        ]
        + report["problems"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("key_dir", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = score(args.key_dir, args.result)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else text(report))


if __name__ == "__main__":
    main()
