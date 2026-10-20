"""A workflow's CI: one run of its tests, each level proving one layer (the V-model).

    static       the design holds on the graph as it is: no drift from the contract, every stage's
                 LLD rules (ports wired, parts realized, no orphan nodes), structure, App = promises
    integration  each stage's Checks passed on a run of exactly this graph (the HLD)
    acceptance   the contract's examples passed through the API on exactly this graph (the contract)
    regression   nothing that passed in the last published version fails now

A run is green when every level passed; publishing needs a green run of exactly this graph. All the
tests live in the workflow itself (examples in its contract, Checks in its stages), so they are
versioned with it. Pure: the route in workflow_api.py stores each run as a version of the workflow.
"""

from __future__ import annotations

from .architecture import COMPLETENESS, check
from .lld import lld_gaps
from .progress import app_gaps
from .workflow_api import prompt_hash
from .workflow_contract import report

LABELS = {
    "static": "design",
    "integration": "stage Checks",
    "acceptance": "examples",
    "regression": "no regression",
}
MARK = {"passed": "✓", "failed": "✗", "not_run": "○"}


def stage(name: str, status: str, detail: list[str]) -> dict:
    return {"stage": name, "label": LABELS[name], "status": status, "detail": detail}


def run(contract: dict, evidence: dict, acceptance: list[dict], published: dict | None) -> dict:
    """One CI run of the workflow in `evidence` (the browser's: its graph, prompt and latest run)."""
    workflow, current = evidence.get("workflow") or {}, prompt_hash(evidence.get("prompt") or {})
    status = report(contract, evidence, acceptance[-1] if acceptance else None)

    problems = [
        *status["drift"],
        *lld_gaps(workflow),
        *(f"{v['where']}: {v['problem']}" for v in check(workflow) if v["kind"] != COMPLETENESS),
        *app_gaps(contract, workflow),
    ]
    stages = [stage("static", "failed" if problems else "passed", list(dict.fromkeys(problems)))]

    # Steps whose gate a person confirms have no Check: their stage is verified by the user, not here.
    checked = {s["id"] for s in contract["steps"] if (s.get("gate") or {}).get("check")}
    states = {s["step"]: s["state"] for s in status["steps"]}
    failing = [sid for sid in checked if states.get(sid) == "failing"]
    waiting = [sid for sid in checked if states.get(sid) != "verified" and sid not in failing]
    stages.append(
        stage("integration", "failed", [f"{sid}: its Checks fail" for sid in failing])
        if failing
        else stage("integration", "not_run", [f"{sid}: run the workflow" for sid in waiting])
        if waiting
        else stage("integration", "passed", [])
    )

    examples = contract.get("examples") or []
    here = [a for a in acceptance if a.get("prompt_hash") == current]
    if not examples:
        stages.append(stage("acceptance", "passed", ["The contract has no examples."]))
    elif not here:
        stages.append(stage("acceptance", "not_run", ["Run the examples on this exact graph."]))
    else:
        last = here[-1]
        misses = [
            f"{r['example']}: {'; '.join(r['misses'][:2])}"
            for r in last["results"]
            if not r["passed"]
        ]
        stages.append(stage("acceptance", "failed" if misses else "passed", misses))

    passed = {
        "examples": sorted(
            r["example"] for r in (here[-1]["results"] if here else []) if r["passed"]
        ),
        "gates": sorted(sid for sid in checked if states.get(sid) == "verified"),
    }
    before = (published or {}).get("checks")
    if not before:
        stages.append(stage("regression", "passed", ["First release: nothing to compare with."]))
    else:
        lost = [
            f"example {e} passed in {published['semver']}"
            for e in before["examples"]
            if e not in passed["examples"]
        ]
        lost += [
            f"{g}'s Checks passed in {published['semver']}"
            for g in before["gates"]
            if g not in passed["gates"]
        ]
        stages.append(stage("regression", "failed" if lost else "passed", lost))

    return {
        "prompt_hash": current,
        "stages": stages,
        "green": all(s["status"] == "passed" for s in stages),
        "checks": passed,
        "summary": " · ".join(f"{MARK[s['status']]} {s['label']}" for s in stages),
    }
