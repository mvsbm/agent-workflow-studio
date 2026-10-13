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
    for step in contract["steps"]:
        check = step["gate"].get("check")
        if check and check["kind"] != "non_empty" and "value" not in check:
            raise ValueError(f"Step {step['id']}: a {check['kind']} check needs a value.")
    return contract


def validate_for_approval(contract: object) -> dict:
    contract = validate_contract(contract)
    if contract["open_questions"]:
        raise ValueError(
            "Resolve the open questions with the user (comfy_ask_user) before asking for "
            "approval: " + "; ".join(contract["open_questions"])
        )
    return contract


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
            "purpose": plan["goal"][:400],
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


def gates(contract: dict) -> list[dict]:
    """Gate checks in the shape the browser's evaluator expects."""
    out = []
    for step in contract["steps"]:
        check = step["gate"].get("check")
        if check:
            check = {
                "node_title": check["node"],
                "kind": check["kind"],
                **({"value": check["value"]} if "value" in check else {}),
            }
        out.append({"step": step["id"], "description": step["gate"]["says"], "check": check})
    return out


def derive_states(contract: dict, nodes: list[dict], gate_results: dict[str, str]) -> list[dict]:
    """Each step's state from evidence, by folding canonical events through TRANSITIONS.

    nodes: canvas nodes as {"step": id|None, "fuzzy": bool}; gate_results: step -> status of
    the latest gate check since the last structural change ("passed", "failed", ...).
    """
    result = []
    for step in contract["steps"]:
        mine = [n for n in nodes if n.get("step") == step["id"]]
        events = []
        if mine:
            events.append("nodes_added")
        if any(n.get("fuzzy") for n in mine):
            events.append("fuzzy_detected")
        outcome = gate_results.get(step["id"])
        if outcome in ("passed", "failed"):
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
                "next": NEXT_ACTIONS[state],
            }
        )
    return result


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
        check = step["gate"].get("check")
        how = (
            f" (checked automatically on “{check['node']}”)" if check else " (you confirm this one)"
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
        ("OPEN QUESTIONS", contract["open_questions"]),
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
    "gates",
    "render",
    "to_yaml",
    "validate_contract",
    "validate_for_approval",
]
