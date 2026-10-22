"""Group matched records, strongest first, never joining records decided different (the guard),
and keep each group's id across runs."""

from __future__ import annotations

from collections import Counter, defaultdict


def guard_clusters(decisions: list[dict], max_size: int, scores_separate: bool = False) -> dict:
    """Merge matches strongest first; refuse merges joining records decided different (or too big).
    With `scores_separate`, a pair the score decided different also keeps its records apart."""
    parent, members, cannot = {}, {}, defaultdict(set)

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in decisions:
        for side in (d["left"], d["right"]):
            if side and side not in parent:
                parent[side], members[side] = side, {side}
    # Cannot-link: pairs a rule or the user decided are different people (score bands are not proof).
    for d in decisions:
        if (
            d["decision"] == "different"
            and (scores_separate or not d["branch"].startswith("Score"))
            and d["left"]
            and d["right"]
        ):
            cannot[d["left"]].add(d["right"])
            cannot[d["right"]].add(d["left"])

    def strength(d: dict) -> tuple:  # your decisions, then rules (no score), then highest score
        return (d["branch"] == "Your review", d["score"] in ("", None), float(d["score"] or 0))

    merged, blocked, joins = [], [], []
    for d in sorted(
        (d for d in decisions if d["decision"] == "match" and d["left"] and d["right"]),
        key=strength,
        reverse=True,
    ):
        a, b = find(d["left"]), find(d["right"])
        if a == b:
            merged.append(d)
            continue
        small, large = sorted((a, b), key=lambda r: len(members[r]))
        conflict = next(
            ((x, y) for x in members[small] for y in cannot[x] if find(y) == large), None
        )
        if conflict:
            blocked.append(
                {**d, "reason": f"would join {conflict[0]} and {conflict[1]}, decided different"}
            )
            continue
        if max_size and len(members[a]) + len(members[b]) > max_size:
            blocked.append({**d, "reason": f"cluster would exceed {max_size} records"})
            continue
        parent[small] = large
        members[large] |= members.pop(small)
        merged.append(d)
        joins.append(d)  # this merge joined two groups: the chain that explains a cluster
    clusters = {}
    for root, group in members.items():
        for mention in group:
            clusters[mention] = root
    return {"clusters": clusters, "merged": merged, "blocked": blocked, "joins": joins}


def stable_ids(clusters: dict, previous: dict) -> tuple[dict, dict]:
    """Cluster ids that survive a re-run: each cluster keeps the previous id most of its records
    had (larger clusters choose first); only new clusters get new ids. Returns (root -> id, diff)."""
    members = defaultdict(list)
    for mention, root in clusters.items():
        members[root].append(mention)
    taken, ids = set(), {}
    for root in sorted(members, key=lambda r: (-len(members[r]), r)):
        votes = Counter(previous[m] for m in members[root] if m in previous)
        ids[root] = next((cid for cid, _ in votes.most_common() if cid not in taken), None)
        taken.add(ids[root])
    numbers = [int(c[1:]) for c in previous.values() if c[1:].isdigit()]
    fresh = max(numbers, default=0)
    for root in sorted(members):
        if ids[root] is None:
            fresh += 1
            ids[root] = f"C{fresh:06d}"
    moved = sum(previous.get(m) not in (None, ids[r]) for m, r in clusters.items())
    kept = len(taken - {None})
    diff = {
        "kept": kept,
        "new": len(members) - kept,
        "gone": len(set(previous.values()) - set(ids.values())),
        "records moved": moved,
    }
    return ids, diff
