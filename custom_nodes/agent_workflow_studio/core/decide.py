"""Decide each scored pair: your review first, then the score bands (the business's precision /
recall dial); optionally a match also needs a second identifier (precision first)."""

from __future__ import annotations

COLUMNS = ["id", "left", "right", "decision", "branch", "score", "why"]


def decide_by_score(
    rows: list[dict], match_at: float, review_from: float, corroboration: bool = False
) -> list[dict]:
    """rows: id, left, right, score, label (same / different / empty), corroborated (0/1)."""
    if review_from > match_at:
        raise ValueError("Review from must not be above Match at.")
    out = []
    for r in rows:
        score = float(r["score"])
        label = r.get("label") or ""
        if label in ("same", "different"):
            d = (
                "match" if label == "same" else "different",
                "Your review",
                f"you decided: {label}",
            )
        elif score >= match_at and corroboration and not int(r.get("corroborated") or 0):
            d = (
                "review",
                "Not corroborated",
                "likely the same, but only name and birth date agree",
            )
        elif score >= match_at:
            d = ("match", f"Score ≥ {match_at:g}", f"{score:.0%} likely the same")
        elif score >= review_from:
            d = ("review", f"Score {review_from:g} to {match_at:g}", f"{score:.0%} likely the same")
        else:
            d = ("different", f"Score < {review_from:g}", f"{score:.0%} likely the same")
        out.append(
            {
                "id": r["id"],
                "left": r["left"],
                "right": r["right"],
                "decision": d[0],
                "branch": d[1],
                "score": round(score, 4),
                "why": d[2],
            }
        )
    return out
