"""Loop engineering: the improve loop as a first-class, auditable thing.

Goal: every step verified (its Checks pass on the latest run of exactly this graph) and every
contract example passing. Each iteration is journaled as its own version: the agent's hypothesis
and change, the measured state before and after, and a verdict the studio computes (the agent does
not judge its own work). A deterministic controller says what comes next: continue, stop (goal
met), revert (the change made things worse), or escalate to the user (budget spent, no progress).
"""

from __future__ import annotations

from typing import Any

BUDGET = {"iterations": 6, "stall": 2}  # defaults; a contract's `loop` may override them
# The version message of an agent edit made while improving, but outside an iteration (not measured).
OUTSIDE = "Outside the improve loop: "


def outside_loop(log: list[dict]) -> list[str]:
    """Unmeasured changes since the latest iteration (log newest first; [] before any iteration)."""
    found = []
    for commit in log:
        if commit["message"].startswith("Loop "):
            return found
        if commit["message"].startswith(OUTSIDE):
            found.append(commit["message"][len(OUTSIDE) :])
    return []  # no iteration yet: building, not improving


def snapshot(status: dict, acceptance: dict | None) -> dict:
    """The measured state: which steps are verified or failing, and how many examples pass."""
    steps = status.get("steps") or []
    return {
        "verified": sorted(s["step"] for s in steps if s["state"] == "verified"),
        "failing": sorted(s["step"] for s in steps if s["state"] == "failing"),
        "steps": len(steps),
        "examples": [acceptance["passed"], acceptance["total"]] if acceptance else None,
        "current": status.get("freshness", "").startswith("Based on"),
    }


def score(state: dict) -> tuple:
    examples = state.get("examples") or [0, 0]
    return (len(state["verified"]) - len(state["failing"]), examples[0])


def verdict(before: dict, after: dict) -> str:
    """improved / worse / same, from the measured states only."""
    if not after.get("current"):
        return "not measured"  # the graph changed since the last run: run and check first
    b, a = score(before), score(after)
    return "improved" if a > b else "worse" if a < b else "same"


def goal_met(state: dict) -> bool:
    examples = state.get("examples")
    return (
        state.get("current", False)
        and state["steps"] > 0
        and len(state["verified"]) == state["steps"]
        and (examples is None or examples[0] == examples[1])
    )


def next_action(journal: list[dict], state: dict, budget: dict) -> dict:
    """What the loop does next, and why — the same answer for the agent and the user."""
    done = [j for j in journal if j.get("verdict") not in (None, "not measured")]
    used = len(done)
    if goal_met(state):
        return {
            "next": "stop",
            "why": "the goal is met: every step verified and every example passing",
        }
    # Revert only while the state is still worse than before that iteration (a restore settles it).
    if done and done[-1]["verdict"] == "worse" and score(state) < score(done[-1]["before"]):
        return {
            "next": "revert",
            "why": f"iteration {done[-1]['n']} made things worse",
            "restore": done[-1].get("version_before"),
        }
    stalled = 0
    for j in reversed(done):
        if j["verdict"] == "improved":
            break
        stalled += 1
    if stalled >= budget["stall"]:
        return {
            "next": "escalate",
            "why": f"no progress in the last {stalled} iterations: ask the user",
        }
    if used >= budget["iterations"]:
        return {
            "next": "escalate",
            "why": f"the budget of {budget['iterations']} iterations is spent: ask the user",
        }
    return {
        "next": "continue",
        "why": f"{len(state['failing'])} failing step(s); iteration {used + 1} of {budget['iterations']}",
    }


def budget_of(contract: dict | None) -> dict:
    return {**BUDGET, **((contract or {}).get("loop") or {})}


def summary(journal: list[dict]) -> list[str]:
    """One line per iteration, so tried ideas are not tried again."""
    return [
        f"#{j['n']} {j.get('verdict', 'running')}: {j['hypothesis']} — changed {j['change']}"
        for j in journal
    ]


__all__: list[Any] = ["budget_of", "goal_met", "next_action", "snapshot", "summary", "verdict"]
