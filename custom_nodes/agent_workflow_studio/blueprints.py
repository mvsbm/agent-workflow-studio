"""Recurring stage patterns as ComfyUI subgraph blueprints, taken from the nayose reference so they
stay in sync with it. ComfyUI lists a pack's subgraphs/*.json in its node library (Subgraph
Blueprints). A blueprint is a pattern, not a step of someone's contract: the contract, request and
LLD tags are left out (the agent tags the nodes again when it builds a stage from one).

Regenerate after rebuilding the reference:  (from the ComfyUI folder)
    .venv/bin/python -m custom_nodes.agent_workflow_studio.blueprints
tests/unit/test_blueprints.py fails when one is missing or stale.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "example_workflows" / "Studio - Nayose reference.json"
OUT = HERE / "subgraphs"
# Blueprint name → (the reference stage it comes from, what it is for).
PATTERNS = {
    "Clean one source system": (
        "clean_policies",
        "One legacy system's export read and cleaned in its own lane: roles stacked into one row per "
        "person, names, kana, dates, codes and phones normalized. Point Read table at your file and "
        "change the column plan for your system.",
    ),
    "Decide by rules": (
        "decide_rules",
        "Pairs decided by readable rules (the first rule that applies decides): certain matches and "
        "certain non-matches leave here; the rest goes on, unsettled.",
    ),
    "Learn points and score": (
        "decide_score",
        "Points per field level learned from the pairs themselves (no answer key), then each unsettled "
        "pair scored: match above one threshold, review between, different below.",
    ),
    "Group into customers": (
        "group_customers",
        "Matched pairs merged into customers strongest first, refusing any merge that would join two "
        "records decided different, with each customer's evidence shown.",
    ),
}


def _bare(properties: dict) -> dict:
    return {k: v for k, v in (properties or {}).items() if not k.startswith("aws_")}


def blueprint(workflow: dict, step: str, name: str, about: str) -> dict:
    """One stage of the workflow as a native blueprint file."""
    stage = next(
        n for n in workflow["nodes"] if (n.get("properties") or {}).get("aws_stage") == step
    )
    definition = copy.deepcopy(
        next(d for d in workflow["definitions"]["subgraphs"] if d["id"] == stage["type"])
    )
    definition["name"] = name
    definition["description"] = about
    for node in definition.get("nodes", []):
        node["properties"] = _bare(node.get("properties"))
    node = copy.deepcopy(stage)
    node.update(id=1, pos=[0, 0], title=name, properties=_bare(node.get("properties")))
    for slot in node.get("inputs", []):
        slot["link"] = None
    for slot in node.get("outputs", []):
        slot["links"] = []
    return {
        "revision": 0,
        "last_node_id": 1,
        "last_link_id": 0,
        "nodes": [node],
        "links": [],
        "version": 0.4,
        "definitions": {"subgraphs": [definition]},
        "extra": {"BlueprintDescription": about},
    }


def blueprints() -> dict[str, str]:
    """File name → content, for every pattern."""
    workflow = json.loads(REFERENCE.read_text(encoding="utf-8"))
    return {
        f"{name}.json": json.dumps(
            blueprint(workflow, step, name, about), indent=1, ensure_ascii=False
        )
        + "\n"
        for name, (step, about) in PATTERNS.items()
    }


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for file, text in blueprints().items():
        (OUT / file).write_text(text, encoding="utf-8")
    print(f"Wrote {len(PATTERNS)} blueprints to {OUT}")
