"""Plan vs built vs proven, side by side, with every gap named: what the Contract tab shows and the
agent's status tool reports, so neither can claim more than the evidence shows.

Three layers of the plan are compared with the canvas and its runs:
- contract: the user's requests (must/should), examples, promised inputs and outputs;
- HLD: each step's typed ports, how, why and the gate it agreed to ("done when" + the exact check);
- LLD: the node types the step was designed with, against the nodes actually in its stage.
"""

from __future__ import annotations

from collections import defaultdict

from .workflow_contract import shallow

SEVERITY = ["blocking", "unproven", "yours", "design", "deferred"]


def agreed_check(step: dict) -> dict | None:
    check = (step.get("gate") or {}).get("check")
    return check if isinstance(check, dict) and check.get("output") else None


def describe(check: dict) -> str:
    expect = check.get("expect", check.get("value"))
    return f"{check.get('kind')}{f' {expect}' if expect not in (None, '') else ''} on {check.get('output') or check.get('watches')}"


def clauses(step: dict) -> list[str]:
    """What the HLD step says, clause by clause: each one some LLD node must realize."""
    return [
        *(f"takes {port}" for port in step.get("takes", {})),
        "does",
        *(f"gives {port}" for port in step.get("gives", {})),
    ]


def tags(node: dict) -> list[str]:
    found = node.get("realizes")
    return [found] if isinstance(found, str) else [t for t in found or [] if isinstance(t, str)]


def realization(step: dict, nodes: list[dict], checks: list[dict]) -> dict:
    """HLD → LLD for one step: each clause and the nodes realizing it (aws_realizes), the gate and
    its Checks, the nodes that realize nothing in the design, and whether the stage is traced."""
    by: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for tag in tags(node):
            by[tag].append(node.get("title") or node.get("class_type") or "?")
    rows = [{"clause": c, "by": by.get(c, [])} for c in clauses(step)]
    if (step.get("gate") or {}).get("says"):
        rows.append({"clause": "gate", "by": [f"Check · {describe(c)}" for c in checks]})
    known = {r["clause"] for r in rows} | {"why"}
    return {
        "rows": rows,
        "traced": any(tags(n) for n in nodes),
        "extra": [n.get("title") or n.get("class_type") for n in nodes if not tags(n)],
        "unknown": sorted(
            {t for n in nodes for t in tags(n) if t not in known and not t.startswith("decision ")}
        ),
    }


def app_gaps(contract: dict, workflow: dict | None) -> list[str]:
    """Where App mode (extra.linearData) differs from the contract's promises: each promised input
    must be asked for, each promised output shown through its API output node, nothing else asked."""
    if not workflow:
        return []
    linear = (workflow.get("extra") or {}).get("linearData") or {}
    asked = [(str(item[0]), item[1]) for item in linear.get("inputs") or [] if len(item) >= 2]
    shown = {str(o) for o in linear.get("outputs") or []}
    nodes = {str(n.get("id")): n for n in workflow.get("nodes") or []}
    promises = contract.get("promises") or {}
    found, gaps = set(), []
    for item in promises.get("inputs", []):
        match = [
            (n, w)
            for n, w in asked
            if (str(item.get("node")) == n and w == item.get("widget", "value"))
            or (nodes.get(n, {}).get("properties") or {}).get("aws_input") == item["name"]
        ]
        found |= set(match)
        if not match:
            gaps.append(f"input {item['name']} is promised, but App mode does not ask for it")
    for n, w in sorted(set(asked) - found):
        gaps.append(
            f"App mode asks for {nodes.get(n, {}).get('title') or n} · {w}, which the contract does not promise"
        )
    for item in promises.get("outputs", []):
        node = next(
            (
                n
                for n in nodes.values()
                if (n.get("properties") or {}).get("aws_output") == item["name"]
            ),
            None,
        )
        if node is None:
            gaps.append(f"output {item['name']}: no output node for App mode to show")
        elif str(node.get("id")) not in shown:
            gaps.append(f"output {item['name']} is promised, but App mode does not show it")
    return gaps


def check_gaps(step: dict, built: list[dict]) -> list[str]:
    """How the stage's Checks differ from the check its gate agreed to."""
    agreed = agreed_check(step)
    if not agreed:
        return []
    same_output = [c for c in built if c.get("watches") == agreed["output"]]
    exact = [
        c
        for c in same_output
        if c.get("kind") == agreed.get("kind")
        and str(c.get("expect") or "") == str(agreed.get("value") or "")
    ]
    if exact:
        return []
    if same_output:
        return [
            f"its Check differs from the one agreed: agreed {describe(agreed)}, built "
            + "; ".join(describe(c) for c in same_output)
        ]
    return [f"the agreed Check ({describe(agreed)}) is missing"]


def merged(gaps: list[dict]) -> list[dict]:
    """One line per kind of gap: "steps a, b: verified only by …", most serious first."""
    groups: dict[tuple[str, str], list[str]] = {}
    for g in gaps:
        groups.setdefault((g["severity"], g["text"]), []).append(g["where"])
    out = []
    for (severity, text), wheres in groups.items():
        kinds = {w.split(" ", 1)[0] for w in wheres}
        if len(wheres) > 1 and len(kinds) == 1 and all(" " in w for w in wheres):
            where = f"{kinds.pop()}s " + ", ".join(w.split(" ", 1)[1] for w in wheres)
        else:
            where = "; ".join(wheres)
        out.append({"severity": severity, "where": where, "text": text, "count": len(wheres)})
    return sorted(out, key=lambda g: SEVERITY.index(g["severity"]))


def trace(
    contract: dict, evidence: dict, states: list[dict], drift: list[str], acceptance: dict | None
) -> dict:
    """Requests → steps → examples, each step's plan/build/proof, and the gaps, most serious first."""
    from .workflow_api import prompt_hash

    steps = {s["id"]: s for s in contract.get("steps", [])}
    state = {s["step"]: s for s in states}
    stages = {s["step"]: s for s in evidence.get("stages", [])}
    by_step = defaultdict(list)
    for node in evidence.get("nodes", []):
        if node.get("step") and not node.get("scaffold"):
            by_step[node["step"]].append(node)
    gate_checks = evidence.get("gate_checks") or {}
    current = evidence.get("run") or {}
    gaps: list[dict] = []
    shallow_steps: list[str] = []

    def gap(severity: str, where: str, text: str) -> None:
        gaps.append({"severity": severity, "where": where, "text": text})

    # Examples: the last acceptance run, and whether it ran on exactly this graph.
    prompt = evidence.get("prompt")
    fresh = bool(
        acceptance and prompt is not None and acceptance.get("prompt_hash") == prompt_hash(prompt)
    )
    results = {r["example"]: r for r in (acceptance or {}).get("results", [])}

    def example_status(example_id: str) -> str:
        found = results.get(example_id)
        if not found:
            return "not run"
        if not fresh:
            return "passed earlier" if found["passed"] else "failed earlier"
        return "passed" if found["passed"] else "failed"

    examples = [
        {
            "id": e["id"],
            "covers": e.get("covers", []),
            "status": example_status(e["id"]),
            "misses": results.get(e["id"], {}).get("misses", []),
        }
        for e in contract.get("examples", [])
    ]
    for e in examples:
        if e["status"] == "failed":
            gap("blocking", f"example {e['id']}", "fails: " + "; ".join(e["misses"][:3]))

    requests = []
    for r in contract.get("requests", []):
        mine = [e for e in examples if r["id"] in e["covers"]]
        doers = [sid for sid, s in steps.items() if r["id"] in (s.get("covers") or [])]
        statuses = {e["status"] for e in mine}
        verdict = (
            "failing"
            if "failed" in statuses
            else "proven"
            if mine and statuses == {"passed"}
            else "proven earlier"
            if mine and statuses <= {"passed", "passed earlier"}
            # No example, but every step that serves it passes its own check: less than proven.
            else "checked by its steps"
            if not mine
            and doers
            and all(state.get(d, {}).get("state") == "verified" for d in doers)
            else "unproven"
        )
        must = r.get("priority") == "must"
        if not doers:
            gap("design", f"request {r['id']}", "no step says it covers this request")
        if must and not mine:
            gap("unproven", f"request {r['id']}", "a must-have with no example to prove it")
        elif must and verdict in ("unproven", "proven earlier"):
            gap(
                "unproven",
                f"request {r['id']}",
                "examples have not run on this version"
                if verdict == "proven earlier"
                else "examples have not run",
            )
        requests.append(
            {
                "id": r["id"],
                "priority": r.get("priority"),
                "you_said": r.get("you_said"),
                "we_understood": r.get("we_understood"),
                "steps": [{"id": sid, "state": state.get(sid, {}).get("state")} for sid in doers],
                "examples": [{"id": e["id"], "status": e["status"]} for e in mine],
                "verdict": verdict,
            }
        )

    rows = []
    for sid, step in steps.items():
        s = state.get(sid, {})
        stage = stages.get(sid, {})
        built = by_step[sid]
        checks = stage.get("checks") or []
        built_nodes = [n for n in built if n.get("class_type") != "AgentWorkflowCheck"]
        types = {n.get("class_type") for n in built_nodes} - {None}
        realized = realization(step, built_nodes, checks)
        where = f"step {sid}"
        mismatch = check_gaps(step, checks)
        for text in mismatch:
            gap("unproven", where, text)
        if s.get("state") == "verified" and checks and all(shallow(c) for c in checks):
            shallow_steps.append(sid)
            gap(
                "unproven", where, "verified only by a Check that data exists, not that it is right"
            )
        if s.get("state") == "failing":
            gap("blocking", where, "its Check fails on the latest run")
        elif s.get("state") in ("built", "fuzzy") and built:
            gap(
                "unproven",
                where,
                "built but not shown to work: run it"
                if not current.get("current")
                else "built, but its Checks did not pass",
            )
        elif s.get("state") == "planned":
            gap("unproven", where, "not built yet")
        if stage.get("stale"):
            gap("unproven", where, "its design changed after it was built")
        # LLD → HLD: every clause realized by some node, every node realizing part of the design.
        if built_nodes and not realized["traced"]:
            gap("design", where, "its nodes do not say which part of the design they realize")
        elif realized["traced"]:
            if missing := [
                r["clause"] for r in realized["rows"] if not r["by"] and r["clause"] != "gate"
            ]:
                gap("design", where, "no node realizes: " + ", ".join(missing))
            if realized["extra"]:
                gap(
                    "design",
                    where,
                    "+ extra, in no part of the design: " + ", ".join(realized["extra"]),
                )
            if realized["unknown"]:
                gap(
                    "design",
                    where,
                    "realizes what the design does not say: " + ", ".join(realized["unknown"]),
                )
        if s.get("waiting"):
            gap("yours", where, "waits for your answer: " + "; ".join(s["waiting"]))
        if open_ := (evidence.get("open") or {}).get(sid):
            gap("yours", where, f"{open_:,} items wait for your review")
        rows.append(
            {
                "id": sid,
                "state": s.get("state"),
                "agreed": {
                    "purpose": step.get("purpose"),
                    "takes": step.get("takes", {}),
                    "gives": step.get("gives", {}),
                    "uses": step.get("uses"),
                    "why": step.get("why"),
                    "pattern": step.get("pattern"),
                    "done_when": (step.get("gate") or {}).get("says"),
                    "check": describe(agreed_check(step)) if agreed_check(step) else None,
                },
                "built": {
                    "nodes": [n.get("title") or n.get("class_type") for n in built][:40],
                    "types": sorted(types),
                    "checks": [describe(c) for c in checks],
                    "fuzzy": sum(bool(n.get("fuzzy")) for n in built),
                },
                "realization": realized,
                "proven": {
                    "gate": s.get("state"),
                    "checks": gate_checks.get(sid, []),
                    "check_matches_agreement": not mismatch,
                },
            }
        )

    for item in (contract.get("promises") or {}).get("outputs", []):
        source = str(item.get("from") or "")
        if source.split(".")[0] not in steps:
            gap("design", f"output {item.get('name')}", "no step gives it")
    for question in contract.get("open_questions", []):
        text = question if isinstance(question, str) else question.get("question")
        gap("yours", "open question", text)
    for item in contract.get("not_now", []):
        gap("deferred", "agreed not now", item if isinstance(item, str) else str(item))
    for item in drift:
        gap("blocking", "drift", item)
    # The contract's promises are the App's interface: App mode, the API and the examples share it.
    for item in app_gaps(contract, evidence.get("workflow")):
        gap("design", "app", item)

    gaps = merged(gaps)
    musts = [r for r in requests if r["priority"] == "must"]
    counts = {k: sum(g["severity"] == k for g in gaps) for k in SEVERITY}
    summary = (
        f"Must-haves proven {sum(r['verdict'] == 'proven' for r in musts)}/{len(musts)} · "
        f"examples passing {sum(e['status'] == 'passed' for e in examples)}/{len(examples)} on this version · "
        f"steps verified {sum(r['state'] == 'verified' for r in rows)}/{len(rows)}"
        + (f", {len(shallow_steps)} of them only shallowly" if shallow_steps else "")
    )
    return {
        "summary": summary,
        "gap_counts": counts,
        "gaps": gaps,
        "requests": requests,
        "examples": examples,
        "steps": rows,
    }
