"""The architecture as checkable rules over a saved workflow (the JSON that Versions stores).

Each rule has a kind that says when it must hold, so unfinished work is legal:
- structural: always; everything derived (cards, wire labels, status, API) depends on it.
- completeness: before a step counts as verified; until then it is the step's to-do.
- release: before the workflow is published as an API.

A rule is a plain function (workflow) -> [(where, problem)].
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

STRUCTURAL, COMPLETENESS, RELEASE = "structural", "completeness", "release"
UNTYPED = {"*", "DICT", ""}  # a port that says nothing about its data
# A gate is a Check node; a node that only shows data (most are ComfyUI output nodes) is not one.
CHECKS = {"AgentWorkflowCheck"}
# Nodes where a model decides at run time. Workflow code (an Agent Tool) is not one: it is approved,
# typed and tested (the "tool tests" level of Run checks), so it is deterministic like a node.
FUZZY = {"AgentWorkflowFuzzyNode", "AgentWorkflowPiStructuredAgent"}
RULES: list[tuple[str, str, Callable]] = []


def rule(kind: str, fix: str) -> Callable:
    def register(check: Callable) -> Callable:
        RULES.append((kind, fix, check))
        return check

    return register


def _definitions(wf: dict) -> dict:
    return {d["id"]: d for d in (wf.get("definitions") or {}).get("subgraphs", [])}


def _title(node: dict, defs: dict) -> str:
    return node.get("title") or defs.get(node["type"], {}).get("name") or node["type"]


def _stages(wf: dict) -> list[tuple[dict, dict]]:
    defs = _definitions(wf)
    return [(n, defs[n["type"]]) for n in wf.get("nodes", []) if n["type"] in defs]


def _inner_types(definition: dict, defs: dict, depth: int = 0) -> list[str]:
    """Node types inside a stage, nested stages included."""
    types = []
    for node in definition.get("nodes", []):
        if node["type"] in defs and depth < 8:
            types += _inner_types(defs[node["type"]], defs, depth + 1)
        else:
            types.append(node["type"])
    return types


def _loose(node: dict) -> bool:
    # Given data and the API's input and output nodes are the workflow's interface, not stray work.
    props = node.get("properties") or {}
    return (
        not props.get("aws_given")
        and not props.get("aws_input")
        and not props.get("aws_output")
        and not node["type"].endswith("Note")
    )


@rule(STRUCTURAL, "Build every node inside its step's stage.")
def node_in_a_stage(wf: dict) -> list:
    defs = _definitions(wf)
    return [
        (_title(n, defs), "is outside every stage")
        for n in wf.get("nodes", [])
        if n["type"] not in defs and _loose(n)
    ]


@rule(STRUCTURAL, "A stage is a contract step: give it the step it builds.")
def stage_is_a_step(wf: dict) -> list:
    return [
        (_title(n, _definitions(wf)), "belongs to no contract step")
        for n, _ in _stages(wf)
        if not (n.get("properties") or {}).get("aws_stage") and _loose(n)
    ]


@rule(
    COMPLETENESS,
    "Give the port a registered data type: TABLE or a dataset class (see /agent-workflow-studio/standards).",
)
def ports_typed(wf: dict) -> list:
    return [
        (
            definition["name"],
            f"{side[:-1]} {port['name']} is untyped ({port.get('type') or 'none'})",
        )
        for node, definition in _stages(wf)
        if _loose(node)
        for side in ("inputs", "outputs")
        for port in definition.get(side, [])
        if port.get("type") in UNTYPED
    ]


@rule(COMPLETENESS, "Add a check that proves the stage worked.")
def stage_checked(wf: dict) -> list:
    defs = _definitions(wf)
    return [
        (definition["name"], "has no check")
        for node, definition in _stages(wf)
        if _loose(node) and not CHECKS & set(_inner_types(definition, defs))
    ]


@rule(
    RELEASE,
    "Choose the workflow's outputs (and any inputs) in App mode, or with the signature edit.",
)
def has_signature(wf: dict) -> list:
    linear = (wf.get("extra") or {}).get("linearData") or {}
    return [] if linear.get("outputs") else [("workflow", "has no outputs chosen for its API")]


@rule(
    RELEASE,
    "Harden the step into deterministic nodes (or a tested tool), or accept the model's judgment.",
)
def no_fuzzy_steps(wf: dict) -> list:
    defs = _definitions(wf)
    return [
        (definition["name"], "decides with an LLM at run time")
        for node, definition in _stages(wf)
        if _loose(node) and FUZZY & set(_inner_types(definition, defs))
    ]


def check(wf: dict) -> list[dict[str, Any]]:
    """Every violation, with its kind and the fix."""
    return [
        {"rule": fn.__name__, "kind": kind, "where": where, "problem": problem, "fix": fix}
        for kind, fix, fn in RULES
        for where, problem in fn(wf)
    ]
