"""Contract, HLD and LLD, stored natively in the ComfyUI workflow (the single source of truth).

- Contract (intent): `extra.aws_contract`: purpose, requests, examples, promises, not_now,
  open_questions and the decisions the user made. Its promised inputs/outputs are the App's
  (`extra.linearData`).
- HLD (the abstracted workflow): the top-level stage nodes. Each stage carries its step in
  `properties.aws_step` (does/takes/gives/uses/why/pattern/gate/covers), its sentence as the
  subgraph's native `description`, and is wired to the others; `extra.aws_design` holds the data
  entities, the agent's design decisions, the loop budget and the steps' order.
- LLD (the realized workflow): the nodes inside each stage, each tagged with the HLD clause it
  realizes in `properties.aws_realizes`.

The rest of the studio works on one joined contract (the v2 shape): `layers()` reads it from a
workflow, `split()` / `embed()` write it into one.
"""

from __future__ import annotations

import copy

# Inside a stage these are the studio's own scaffolding, not part of the realized design.
SCAFFOLD = {
    "AgentWorkflowCheck",
    "AgentWorkflowApiOutput",
    "AgentWorkflowStub",
    "Note",
    "MarkdownNote",
}
INTENT = ("contract", "purpose", "requests", "examples", "promises", "not_now", "open_questions")


def split(contract: dict) -> tuple[dict, dict, dict[str, dict]]:
    """(intent for extra.aws_contract, design for extra.aws_design, step id → aws_step)."""
    decisions = contract.get("decisions") or []
    intent = {k: copy.deepcopy(contract[k]) for k in INTENT if k in contract}
    intent["decisions"] = [d for d in decisions if d.get("by") == "user"]
    design = {
        "decisions": [d for d in decisions if d.get("by") != "user"],
        "order": [s["id"] for s in contract["steps"]],
        **{k: copy.deepcopy(contract[k]) for k in ("data", "loop") if k in contract},
    }
    return intent, design, {s["id"]: copy.deepcopy(s) for s in contract["steps"]}


def intent_of(contract: dict) -> dict:
    """The contract alone (what the user approved first): no steps, no step behind each output."""
    intent, _, _ = split(contract)
    outputs = [{k: v for k, v in o.items() if k != "from"} for o in intent["promises"]["outputs"]]
    return {**intent, "promises": {**intent["promises"], "outputs": outputs}}


def design_of(contract: dict) -> dict:
    """The design (HLD) part: steps, data, the agent's decisions, the loop and each output's step."""
    _, design, _ = split(contract)
    design.pop("order")
    return {
        **design,
        "steps": copy.deepcopy(contract["steps"]),
        "outputs": {o["name"]: o["from"] for o in contract["promises"]["outputs"] if o.get("from")},
    }


def stage_nodes(workflow: dict) -> dict[str, dict]:
    """Step id → its top-level stage node."""
    return {
        n["properties"]["aws_stage"]: n
        for n in workflow.get("nodes") or []
        if isinstance(n, dict) and (n.get("properties") or {}).get("aws_stage")
    }


def layers(workflow: dict | None) -> dict | None:
    """The joined contract a workflow holds natively, or None when it has no contract.

    A step whose stage is gone keeps its place as {"id", "missing": True}: removing a stage
    changes the design, it does not silently shrink it."""
    extra = (workflow or {}).get("extra") or {}
    intent = extra.get("aws_contract")
    if not isinstance(intent, dict):
        return None
    design = extra.get("aws_design") or {}
    stages = stage_nodes(workflow)
    steps = []
    for step_id in design.get("order") or list(stages):
        step = (stages.get(step_id, {}).get("properties") or {}).get("aws_step")
        steps.append(
            copy.deepcopy(step) if isinstance(step, dict) else {"id": step_id, "missing": True}
        )
    return {
        **copy.deepcopy(intent),
        "steps": steps,
        "decisions": [*intent.get("decisions", []), *design.get("decisions", [])],
        **{k: copy.deepcopy(design[k]) for k in ("data", "loop") if k in design},
    }


def embed(contract: dict, workflow: dict, sentences: dict[str, str] | None = None) -> dict:
    """The workflow with this contract stored natively (a copy). Stages are matched by step id;
    `sentences` (from workflow_contract.hld) become the stage subgraphs' descriptions."""
    out = copy.deepcopy(workflow)
    intent, design, steps = split(contract)
    out["extra"] = {**(out.get("extra") or {}), "aws_contract": intent, "aws_design": design}
    definitions = {d.get("id"): d for d in (out.get("definitions") or {}).get("subgraphs", [])}
    for step_id, node in stage_nodes(out).items():
        if step_id in steps:
            node["properties"] = {**node["properties"], "aws_step": steps[step_id]}
            if sentences and step_id in sentences and node.get("type") in definitions:
                definitions[node["type"]]["description"] = sentences[step_id]
    return out


def infer_realizes(workflow: dict) -> dict:
    """The workflow with each untagged node inside a stage tagged by what its wiring shows (a copy):
    fed by the stage's input port → "takes <port>", feeding its output port → "gives <port>", and
    every working node → "does". A port the step does not take, wired to a widget, is a setting
    promoted to the stage card, not data. A starting point the author confirms; existing tags kept."""
    out = copy.deepcopy(workflow)
    takes = {
        n.get("type"): ((n.get("properties") or {}).get("aws_step") or {}).get("takes") or {}
        for n in stage_nodes(out).values()
    }
    for definition in (out.get("definitions") or {}).get("subgraphs", []):
        if definition.get("id") not in takes:
            continue
        inputs = [i.get("name") for i in definition.get("inputs", [])]
        outputs = [o.get("name") for o in definition.get("outputs", [])]
        boundary_in = (definition.get("inputNode") or {}).get("id", -10)
        boundary_out = (definition.get("outputNode") or {}).get("id", -20)
        found: dict = {}
        slots = {n.get("id"): n.get("inputs") or [] for n in definition.get("nodes", [])}
        for link in definition.get("links", []):
            if link.get("origin_id") == boundary_in and link["origin_slot"] < len(inputs):
                port, target = inputs[link["origin_slot"]], slots.get(link.get("target_id"), [])
                slot = link.get("target_slot", 0)
                if (
                    slot < len(target)
                    and target[slot].get("widget")
                    and port not in takes[definition["id"]]
                ):
                    continue  # a setting on the stage card
                found.setdefault(link["target_id"], []).append(f"takes {port}")
            if link.get("target_id") == boundary_out and link["target_slot"] < len(outputs):
                found.setdefault(link["origin_id"], []).append(
                    f"gives {outputs[link['target_slot']]}"
                )
        for node in definition.get("nodes", []):
            if node.get("type") in SCAFFOLD or "aws_realizes" in (node.get("properties") or {}):
                continue
            node.setdefault("properties", {})["aws_realizes"] = [
                *sorted(set(found.get(node["id"], []))),
                "does",
            ]
    return out
