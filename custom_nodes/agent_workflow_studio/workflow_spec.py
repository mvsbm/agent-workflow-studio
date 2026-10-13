"""The standardized workflow plan the agent must get approved before it builds.

One plan per workflow, stored as ``spec.json`` in the workflow's project history.
It is written for a non-technical reader (goal, steps, how each step's success is
judged) with technical details (nodes, connections) kept separate. Every step has
a success gate; a gate may carry a check that is evaluated against real run outputs.
"""

from __future__ import annotations

import json

from .structured_json import build_validator

GATE_KINDS = ("non_empty", "contains", "equals", "regex", "image_count_at_least")
_TEXT = {"type": "string", "minLength": 1, "maxLength": 500}
_PORT = {
    "type": "object",
    "properties": {"name": _TEXT, "description": _TEXT, "type": _TEXT},
    "required": ["name", "description", "type"],
    "additionalProperties": False,
}
SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": _TEXT,
        "inputs": {"type": "array", "items": _PORT, "maxItems": 16},
        "outputs": {"type": "array", "items": _PORT, "minItems": 1, "maxItems": 16},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$"},
                    "purpose": _TEXT,
                    "approach": {"enum": ["deterministic", "fuzzy", "llm"]},
                    "nodes": {"type": "array", "items": _TEXT, "maxItems": 16},
                    "connections": {"type": "string", "maxLength": 1000},
                    "success_gate": {
                        "type": "object",
                        "properties": {
                            "description": _TEXT,
                            "check": {
                                "type": "object",
                                "properties": {
                                    "node_title": _TEXT,
                                    "kind": {"enum": list(GATE_KINDS)},
                                    "value": {"type": ["string", "integer"]},
                                },
                                "required": ["node_title", "kind"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["description"],
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "purpose", "approach", "success_gate"],
                "additionalProperties": False,
            },
        },
        "assumptions": {"type": "array", "items": _TEXT, "maxItems": 32},
        "decisions": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "properties": {"question": _TEXT, "answer": _TEXT},
                "required": ["question", "answer"],
                "additionalProperties": False,
            },
        },
        "open_questions": {"type": "array", "items": _TEXT, "maxItems": 16},
    },
    "required": [
        "goal",
        "inputs",
        "outputs",
        "steps",
        "assumptions",
        "decisions",
        "open_questions",
    ],
    "additionalProperties": False,
}
_validator = build_validator(json.dumps(SPEC_SCHEMA))


def validate_spec(spec: object) -> dict:
    """Raise ValueError unless the plan is complete and ready for the user's approval."""
    errors = sorted(_validator.iter_errors(spec), key=lambda e: list(e.absolute_path))
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path) or "plan"
        raise ValueError(f"Invalid workflow plan at {where}: {errors[0].message[:200]}")
    ids = [step["id"] for step in spec["steps"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Each step needs a unique id.")
    for step in spec["steps"]:
        check = step["success_gate"].get("check")
        if check and check["kind"] != "non_empty" and "value" not in check:
            raise ValueError(f"Step {step['id']}: a {check['kind']} check needs a value.")
    if spec["open_questions"]:
        raise ValueError(
            "Resolve the open questions with the user (comfy_ask_user) before asking for "
            "approval: " + "; ".join(spec["open_questions"])
        )
    return spec


def _gate(gate: dict) -> str:
    check = gate.get("check")
    if not check:
        return f"{gate['description']} (you confirm this one)"
    how = {
        "non_empty": "is not empty",
        "contains": f"contains “{check.get('value')}”",
        "equals": f"is exactly “{check.get('value')}”",
        "regex": f"matches the pattern {check.get('value')}",
        "image_count_at_least": f"has at least {check.get('value')} image(s)",
    }[check["kind"]]
    return f"{gate['description']} (checked automatically: “{check['node_title']}” {how})"


def render_spec(spec: dict) -> str:
    """Plain-language view for approval; technical details come last."""
    approach = {"deterministic": "fixed steps", "fuzzy": "AI-planned", "llm": "AI judgment"}
    lines = [f"GOAL: {spec['goal']}", ""]
    for label, ports in (("YOU PROVIDE", spec["inputs"]), ("YOU GET", spec["outputs"])):
        if ports:
            lines += [label + ":"] + [f"  • {p['name']}: {p['description']}" for p in ports] + [""]
    lines.append("STEPS:")
    for n, step in enumerate(spec["steps"], 1):
        lines.append(f"  {n}. {step['purpose']} [{approach[step['approach']]}]")
        lines.append(f"     ✓ Done when: {_gate(step['success_gate'])}")
    for label, items in (
        ("ASSUMPTIONS", spec["assumptions"]),
        ("DECISIONS", [f"{d['question']} → {d['answer']}" for d in spec["decisions"]]),
    ):
        if items:
            lines += ["", label + ":"] + [f"  • {item}" for item in items]
    technical = [
        f"  {step['id']}: {', '.join(step.get('nodes', [])) or '(to be chosen)'}"
        + (f" — {step['connections']}" if step.get("connections") else "")
        for step in spec["steps"]
    ]
    return "\n".join(lines + ["", "Technical details (for reference):"] + technical)


__all__ = ["GATE_KINDS", "SPEC_SCHEMA", "render_spec", "validate_spec"]
