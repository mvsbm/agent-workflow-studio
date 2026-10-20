"""The low-level design of one step: the nodes that realize it, each tagged with the HLD clause it
realizes, and their wires. The agent submits it (comfy_build_step); the studio checks it against the
step here, then compiles it into the step's stage (the stage's subgraph is the LLD from then on).

    {"step": "clean_policies",
     "nodes": {"read": {"type": "AgentWorkflowReadTable", "title": "Read policies", "set": {"file": "policies.csv"},
                        "realizes": ["takes folder", "does"]}, ...},
     "wires": [{"from": "input.folder", "to": "read.folder"}, {"from": "read.table", "to": "roles.table"},
               {"from": "split.table", "to": "output.table"}]}

A wire end is "input.<port>" (the stage's input), "output.<port>" (its output) or "<node>.<socket>"
(a socket name, or its index for an output).
"""

from __future__ import annotations

import re

from .native_layers import SCAFFOLD, stage_nodes
from .progress import clauses, tags

KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_NODES = 40


def allowed(step: dict, decisions: list[str]) -> set[str]:
    """What a node may say it realizes: the step's clauses, its reason, and a design decision."""
    return {*clauses(step), "why", *(f"decision {d}" for d in decisions)}


def validate_lld(step: dict, spec: object, decisions: list[str] = ()) -> dict:
    """The spec, or ValueError naming what to fix: every clause realized, every node traced, every
    port of the stage wired, nothing wired to what does not exist."""
    if not isinstance(spec, dict) or not isinstance(spec.get("nodes"), dict) or not spec["nodes"]:
        raise ValueError("Give the step's nodes: {key: {type, set, realizes}}.")
    nodes, wires = spec["nodes"], spec.get("wires") or []
    if len(nodes) > MAX_NODES:
        raise ValueError(f"At most {MAX_NODES} nodes in one stage; split the step in the design.")
    ok = allowed(step, list(decisions))
    realized = set()
    for key, node in nodes.items():
        if not KEY.match(key) or key in ("input", "output"):
            raise ValueError(f"Node key {key!r}: short lowercase words (not input/output).")
        if not isinstance(node, dict) or not isinstance(node.get("type"), str):
            raise ValueError(f"Node {key}: give its type (a node class from the catalog).")
        tags = node.get("realizes")
        tags = [tags] if isinstance(tags, str) else tags
        if not tags or not all(isinstance(t, str) for t in tags):
            raise ValueError(
                f"Node {key}: say which part of the design it realizes ({', '.join(sorted(ok))})."
            )
        if unknown := [t for t in tags if t not in ok]:
            raise ValueError(
                f"Node {key} realizes {', '.join(unknown)}, which the design of {step['id']} does not "
                f"say; use one of: {', '.join(sorted(ok))}."
            )
        node["realizes"] = tags
        realized |= set(tags)
    if missing := [c for c in clauses(step) if c not in realized]:
        raise ValueError(f"No node realizes {', '.join(missing)} of step {step['id']}.")
    takes, gives = set(step.get("takes") or {}), set(step.get("gives") or {})
    used, fed = set(), {}
    for wire in wires:
        if not isinstance(wire, dict) or not all(
            isinstance(wire.get(k), str) for k in ("from", "to")
        ):
            raise ValueError(
                'A wire is {"from": "<node or input>.<socket>", "to": "<node or output>.<socket>"}.'
            )
        source, _, out = wire["from"].partition(".")
        target, _, into = wire["to"].partition(".")
        if not out or not into:
            raise ValueError(
                f"Wire {wire['from']} → {wire['to']}: name both ends as <node>.<socket>."
            )
        if source == "output" or target == "input":
            raise ValueError(
                f"Wire {wire['from']} → {wire['to']}: data flows from input to output."
            )
        if source == "input":
            if out not in takes:
                raise ValueError(
                    f"Step {step['id']} takes no {out} (it takes {', '.join(sorted(takes)) or 'nothing'})."
                )
            used.add(out)
        elif source not in nodes:
            raise ValueError(f"Wire from unknown node {source}.")
        if target == "output":
            if into not in gives:
                raise ValueError(
                    f"Step {step['id']} gives no {into} (it gives {', '.join(sorted(gives))})."
                )
            if into in fed:
                raise ValueError(f"Output {into} is fed twice ({fed[into]} and {wire['from']}).")
            fed[into] = wire["from"]
        elif target not in nodes:
            raise ValueError(f"Wire to unknown node {target}.")
    if unused := sorted(takes - used):
        raise ValueError(f"Step {step['id']} takes {', '.join(unused)}, but no wire uses it.")
    if unfed := sorted(gives - set(fed)):
        raise ValueError(f"Step {step['id']} gives {', '.join(unfed)}, but no wire feeds it.")
    return {"step": step["id"], "nodes": nodes, "wires": wires}


def lld_gaps(workflow: dict) -> list[str]:
    """The LLD rules checked on the stages as they are now (hand edits included), not only when built:
    every port of a stage wired inside it, every part of its step realized by a node, and no node
    inside it that traces to nothing (a traceability orphan)."""
    definitions = {d.get("id"): d for d in (workflow.get("definitions") or {}).get("subgraphs", [])}

    def tagged(node: dict) -> list[str]:
        return tags({"realizes": (node.get("properties") or {}).get("aws_realizes")})

    gaps = []
    for step_id, stage in stage_nodes(workflow).items():
        step, definition = (
            (stage.get("properties") or {}).get("aws_step"),
            definitions.get(stage.get("type")),
        )
        if not step or not definition:
            continue
        inputs = [i.get("name") for i in definition.get("inputs", [])]
        outputs = [o.get("name") for o in definition.get("outputs", [])]
        boundary_in = (definition.get("inputNode") or {}).get("id", -10)
        boundary_out = (definition.get("outputNode") or {}).get("id", -20)
        links = definition.get("links", [])
        used = {
            inputs[x["origin_slot"]]
            for x in links
            if x.get("origin_id") == boundary_in and x["origin_slot"] < len(inputs)
        }
        fed = {
            outputs[x["target_slot"]]
            for x in links
            if x.get("target_id") == boundary_out and x["target_slot"] < len(outputs)
        }
        title = stage.get("title") or definition.get("name") or step_id
        gaps += [
            f"{title}: takes {p}, but nothing inside uses it"
            for p in step.get("takes", {})
            if p not in used
        ]
        gaps += [
            f"{title}: gives {p}, but nothing inside feeds it"
            for p in step.get("gives", {})
            if p not in fed
        ]
        working = [n for n in definition.get("nodes", []) if n.get("type") not in SCAFFOLD]
        realized = {t for n in working for t in tagged(n)}
        gaps += [f"{title}: no node realizes {c}" for c in clauses(step) if c not in realized]
        gaps += [
            f"{title}: {n.get('title') or n.get('type')} realizes nothing in the design (tag it, or remove it)"
            for n in working
            if not tagged(n)
        ]
    return gaps
