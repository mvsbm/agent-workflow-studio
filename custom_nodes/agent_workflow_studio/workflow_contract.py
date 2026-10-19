"""The workflow contract: one per workflow, stored as contract.yaml in its project history.

The contract holds intent only (purpose, requests, examples, promises, steps, decisions,
not-now, open questions), validated against contract.schema.json, the single source of
truth shared with the agent's tool and editors. Step status is never stored: it is
derived from evidence (canvas, runs, gate checks) by folding events through one
transition table, so it cannot drift from reality.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).with_name("contract.schema.json")
SCHEMA = json.loads(SCHEMA_PATH.read_text())
_validator = Draft202012Validator(SCHEMA)
HEADER = "# yaml-language-server: $schema=contract.schema.json\n# Intent only. Step status and technical details come from the canvas and runs.\n"

# Step lifecycle: state x event -> next state. Unlisted events leave the state unchanged.
TRANSITIONS = {
    "planned": {"nodes_added": "built"},
    "built": {"fuzzy_detected": "fuzzy", "gate_passed": "verified", "gate_failed": "failing"},
    "fuzzy": {"hardened": "built", "gate_passed": "verified", "gate_failed": "failing"},
    "verified": {"nodes_changed": "built"},
    "failing": {"nodes_changed": "built", "gate_passed": "verified"},
}
NEXT_ACTIONS = {
    "planned": "Build this step's nodes.",
    "built": "Run the workflow, then check gates.",
    "fuzzy": "Run and check gates; harden the plan once it is right.",
    "verified": "Nothing; the gate passed on the latest run.",
    "failing": "Fix the step's nodes, run again and re-check gates.",
}


def validate_contract(contract: object) -> dict:
    """Raise ValueError unless the contract matches the schema and is internally consistent."""
    errors = sorted(_validator.iter_errors(contract), key=lambda e: list(e.absolute_path))
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path) or "contract"
        raise ValueError(f"Invalid contract at {where}: {errors[0].message[:200]}")
    for section in ("requests", "examples", "steps", "decisions"):
        ids = [item["id"] for item in contract[section]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Each entry in {section} needs a unique id.")
    requests = {r["id"]: r for r in contract["requests"]}
    for item in contract["examples"] + contract["steps"]:
        unknown = set(item["covers"]) - requests.keys()
        if unknown:
            raise ValueError(
                f"{item['id']} covers unknown request(s): {', '.join(sorted(unknown))}"
            )
    covered = {r for step in contract["steps"] for r in step["covers"]}
    missing = [
        r["id"] for r in contract["requests"] if r["priority"] != "could" and r["id"] not in covered
    ]
    if missing:
        raise ValueError(
            f"No step covers request(s) {', '.join(missing)}; add a step or move them to not_now."
        )
    gives = {}
    for step in contract["steps"]:
        check = step["gate"].get("check")
        if check and check["kind"] != "non_empty" and "value" not in check:
            raise ValueError(f"Step {step['id']}: a {check['kind']} check needs a value.")
        if check and "output" in check and check["output"] not in step.get("gives", {}):
            raise ValueError(
                f"Step {step['id']}: the check is on {check['output']}, which the step does not give."
            )
        # Stages are wired from earlier steps only, so the skeleton is a graph that can run.
        for port, source in step.get("takes", {}).items():
            if "." in source:
                src, out = source.split(".")
                if out not in gives.get(src, {}):
                    raise ValueError(
                        f"Step {step['id']} takes {port} from {source}, which no earlier step gives."
                    )
        gives[step["id"]] = step.get("gives", {})
    for q in contract["open_questions"]:
        if isinstance(q, dict) and q["step"] not in gives:
            raise ValueError(f"Open question “{q['question']}” waits on unknown step {q['step']}.")
    return contract


def validate_for_approval(contract: object) -> dict:
    contract = validate_contract(contract)
    # A question tied to one step may stay open (that step waits); any other blocks approval.
    if blocking := [q for q in contract["open_questions"] if isinstance(q, str)]:
        raise ValueError(
            "Resolve the open questions with the user (comfy_ask_user) before asking for "
            "approval, or tie each to the one step that waits for it ({question, step}): "
            + "; ".join(blocking)
        )
    return contract


def waiting(contract: dict) -> dict[str, list[str]]:
    """step -> the open questions it waits for."""
    found: dict[str, list[str]] = {}
    for q in contract.get("open_questions", []):
        if isinstance(q, dict):
            found.setdefault(q["step"], []).append(q["question"])
    return found


def to_yaml(contract: dict) -> str:
    # safe_dump quotes any string YAML would otherwise read as another type ("no", "1.0").
    return HEADER + yaml.safe_dump(contract, sort_keys=False, allow_unicode=True, width=100)


def from_yaml(text: str) -> dict:
    return validate_contract(yaml.safe_load(text))


def from_plan_v1(plan: dict) -> dict:
    """Convert a workflow plan approved before contracts existed (spec.json)."""
    approach = {"deterministic": "fixed", "fuzzy": "ai_planned", "llm": "ai_judgment"}
    steps = []
    for step in plan["steps"]:
        gate = {"says": step["success_gate"]["description"]}
        if check := step["success_gate"].get("check"):
            gate["check"] = {"node": check["node_title"], "kind": check["kind"]}
            if "value" in check:
                gate["check"]["value"] = check["value"]
        converted = {
            "id": step["id"],
            "purpose": step["purpose"],
            "approach": approach[step["approach"]],
            "covers": ["R1"],
            "gate": gate,
        }
        if step.get("nodes"):
            converted["nodes"] = step["nodes"]
        steps.append(converted)
    decisions = [
        {"id": f"D{n}", "decision": d["answer"], "why": d["question"], "by": "user"}
        for n, d in enumerate(plan["decisions"], 1)
    ] + [
        {"id": f"D{n}", "decision": a, "why": "Assumption made while planning.", "by": "agent"}
        for n, a in enumerate(plan["assumptions"], len(plan["decisions"]) + 1)
    ]
    ports = lambda items: [{"name": p["name"], "kind": p["type"]} for p in items]  # noqa: E731
    return validate_contract(
        {
            "contract": 2,
            "purpose": plan["goal"][:600],
            "requests": [
                {
                    "id": "R1",
                    "you_said": plan["goal"][:300],
                    "we_understood": plan["goal"][:300],
                    "priority": "must",
                }
            ],
            "examples": [],
            "promises": {"inputs": ports(plan["inputs"]), "outputs": ports(plan["outputs"])},
            "steps": steps,
            "decisions": decisions[:32],
            "not_now": [],
            "open_questions": plan["open_questions"],
        }
    )


def derive_states(contract: dict, nodes: list[dict], gate_results: dict[str, str]) -> list[dict]:
    """Each step's state from evidence, by folding canonical events through TRANSITIONS.

    nodes: canvas nodes as {"step": the stage it is in, "fuzzy": bool, "scaffold": bool};
    gate_results: step -> status of its Check nodes on the latest run of exactly this graph
    ("passed", "failed", ...). A stage holding only its Stub and Checks is still planned.
    """
    result = []
    for step in contract["steps"]:
        mine = [n for n in nodes if n.get("step") == step["id"] and not n.get("scaffold")]
        events = []
        if mine:
            events.append("nodes_added")
        if any(n.get("fuzzy") for n in mine):
            events.append("fuzzy_detected")
        outcome = gate_results.get(step["id"])
        questions = waiting(contract).get(step["id"], [])
        # A step waiting on the user's answer can fail its gate, but not be verified yet.
        if outcome == "failed" or (outcome == "passed" and not questions):
            events.append(f"gate_{outcome}")
        state = "planned"
        for event in events:
            state = TRANSITIONS[state].get(event, state)
        result.append(
            {
                "step": step["id"],
                "purpose": step["purpose"],
                "state": state,
                "nodes": len(mine),
                "next": f"Waiting on your answer: {'; '.join(questions)}"
                if questions
                else NEXT_ACTIONS[state],
                **({"waiting": questions} if questions else {}),
            }
        )
    return result


def alignment(contract: dict, evidence: dict) -> list[str]:
    """Where the canvas disagrees with the contract (empty when aligned). Not-yet-built steps are
    progress, not drift. evidence: nodes, stages (with their ports) and the graph check."""
    steps = {s["id"]: s for s in contract["steps"]}
    nodes = evidence["nodes"]
    analysis = evidence.get("analysis") or {}
    drift = []
    # Given nodes (provided by the user or a template) are inputs to the work, not steps of it.
    if stray := [
        n.get("title") or "untitled"
        for n in nodes
        if not n.get("step") and not n.get("given") and not n.get("note")
    ]:
        drift.append(f"Outside every stage: {', '.join(stray[:6])}.")
    for stage in evidence.get("stages", []):
        step = steps.get(stage["step"])
        if not step:
            drift.append(f"{stage['title']} is no longer a contract step.")
            continue
        wanted = {
            **{f"takes {k}": v for k, v in step.get("takes", {}).items() if "." not in v},
            **{f"gives {k}": v for k, v in step.get("gives", {}).items()},
        }
        have = {
            **{f"takes {k}": v for k, v in stage.get("takes", [])},
            **{f"gives {k}": v for k, v in stage.get("gives", [])},
        }
        for port, kind in wanted.items():
            if port in have and have[port] != kind and "*" not in (have[port], kind):
                drift.append(f"{stage['title']} {port} as {have[port]}; the contract says {kind}.")
    for step in contract["steps"]:
        built = [n for n in nodes if n.get("step") == step["id"]]
        if step["approach"] == "fixed" and any(n.get("fuzzy") for n in built):
            drift.append(f"Step {step['id']} is fixed in the contract but uses a Fuzzy node.")
    drift += [f"Graph problem: {problem}" for problem in analysis.get("problems", [])[:6]]
    return drift


def todo(evidence: dict) -> list[str]:
    """What the architecture still asks for (unfinished work, not errors), from the saved workflow."""
    from .architecture import COMPLETENESS, check

    if not evidence.get("workflow"):
        return []
    return [
        f"{v['where']}: {v['problem']} — {v['fix']}"
        + ("" if v["kind"] == COMPLETENESS else f" ({v['kind']})")
        for v in check(evidence["workflow"])
        if v["rule"] != "node_in_a_stage"  # already in drift, with the titles
    ]


def report(contract: dict, evidence: dict) -> dict:
    """Where the work stands, from browser evidence: one function for the agent and the Contract tab.

    evidence: {"nodes": [{"title", "step", "fuzzy"}], "gate_results": {step: status} from the latest
    run of exactly the current graph (empty if none), "run": {"prompt_id", "current"} | None}.
    """
    states = derive_states(contract, evidence["nodes"], evidence.get("gate_results") or {})
    run = evidence.get("run")
    freshness = (
        "No run of this workflow yet."
        if not run
        else "Based on the latest run of this exact graph."
        if run.get("current")
        else "The graph changed since the last run; run it again to check gates."
    )
    drift = alignment(contract, evidence)
    return {
        "progress": f"{sum(s['state'] == 'verified' for s in states)}/{len(states)} steps verified",
        "freshness": freshness,
        "aligned": not drift,
        "drift": drift,
        "steps": states,
        "nodes_outside_steps": [
            n.get("title") or "untitled"
            for n in evidence["nodes"]
            if not n.get("step") and not n.get("given") and not n.get("note")
        ],
        "todo": [
            f"Answer: {q} (step {step} waits; amend the contract with the answer as a decision)"
            for step, qs in waiting(contract).items()
            for q in qs
        ]
        + todo(evidence),
        "text": render(contract, states),
    }


def render(contract: dict, states: list[dict] | None = None) -> str:
    """Plain-language view: what a non-technical reader approves; technical bits last."""
    approach = {"fixed": "fixed steps", "ai_planned": "AI-planned", "ai_judgment": "AI judgment"}
    status = {s["step"]: s["state"] for s in states or []}
    lines = [f"PURPOSE: {contract['purpose']}", "", "WHAT YOU ASKED FOR:"]
    for r in contract["requests"]:
        lines += [
            f"  {r['id']} ({r['priority']}) you said: “{r['you_said']}”",
            f"      we understood: {r['we_understood']}",
        ]
    if contract["examples"]:
        lines += ["", "EXAMPLES:"]
        for e in contract["examples"]:
            lines.append(
                f"  {e['id']} given {json.dumps(e['given'], ensure_ascii=False)} → expect {json.dumps(e['expect'], ensure_ascii=False)}"
            )
    promises = contract["promises"]
    for label, ports in (("YOU PROVIDE", promises["inputs"]), ("YOU GET", promises["outputs"])):
        if ports:
            lines += ["", f"{label}:"] + [
                f"  • {p['name']} ({p['kind']})"
                + (f", e.g. {p['example']}" if "example" in p else "")
                for p in ports
            ]
    lines += ["", "STEPS:"]
    for n, step in enumerate(contract["steps"], 1):
        state = f" — {status[step['id']]}" if step["id"] in status else ""
        lines.append(
            f"  {n}. {step['purpose']} [{approach[step['approach']]}] covers {', '.join(step['covers'])}{state}"
        )
        ports = [
            *(f"takes {k} ← {v}" for k, v in step.get("takes", {}).items()),
            *(f"gives {k}: {v}" for k, v in step.get("gives", {}).items()),
        ]
        if ports:
            lines.append(f"     Stage: {' · '.join(ports)}")
        check = step["gate"].get("check")
        how = (
            f" (a Check on {check['output']}: {check['kind']}"
            + (f" {check['value']}" if "value" in check else "")
            + ")"
            if check and check.get("output")
            else " (you confirm this one)"
        )
        lines.append(f"     ✓ Done when: {step['gate']['says']}{how}")
    for label, items in (
        (
            "DECISIONS",
            [
                f"{d['id']} {d['decision']} — why: {d['why']}"
                + (f" (instead of {d['instead_of']})" if d.get("instead_of") else "")
                + f" · by {'you' if d['by'] == 'user' else 'the agent'}"
                for d in contract["decisions"]
            ],
        ),
        ("NOT NOW", contract["not_now"]),
        (
            "OPEN QUESTIONS",
            [
                q if isinstance(q, str) else f"{q['question']} (step {q['step']} waits for it)"
                for q in contract["open_questions"]
            ],
        ),
    ):
        if items:
            lines += ["", f"{label}:"] + [f"  • {item}" for item in items]
    technical = [
        f"  {s['id']}: {', '.join(s['nodes'])}" for s in contract["steps"] if s.get("nodes")
    ]
    if technical:
        lines += ["", "Technical details (for reference):"] + technical
    return "\n".join(lines)


__all__ = [
    "NEXT_ACTIONS",
    "SCHEMA",
    "TRANSITIONS",
    "derive_states",
    "from_plan_v1",
    "from_yaml",
    "render",
    "alignment",
    "report",
    "to_yaml",
    "validate_contract",
    "validate_for_approval",
]
