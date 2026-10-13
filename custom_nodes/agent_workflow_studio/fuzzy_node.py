"""Fuzzy node: the model plans a chain of allowlisted installed nodes; the plan runs as real nodes.

The model never writes code. Its plan is validated locally against the live node
schemas (node names, input names, link types, choices, step order) and then
expanded into ordinary ComfyUI nodes, so every step is visible in the run and
the plan can later be frozen into deterministic nodes.

shortcut: text in, text out only; generalize to typed inputs/outputs when a
non-text fuzzy job needs it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from comfy_api.latest import io
from comfy_execution.graph_utils import GraphBuilder

from .agent_node import record_run
from .llm_harness import LLMRunHarness
from .node_progress import node_id_for
from .pi_cli import complete_json
from .pi_model_config import PiModelConfigSocket, validate_pi_model_config

INPUT = "$input"
MAX_STEPS = 12
STEP_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
DEFAULT_NODES = (
    "StringConcatenate, StringSubstring, StringTrim, StringReplace, CaseConverter, "
    "RegexReplace, RegexExtract"
)
REF = {
    "type": "object",
    "properties": {"from": {"type": "string"}, "output": {"type": "integer", "minimum": 0}},
    "required": ["from"],
    "additionalProperties": False,
}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string", "maxLength": 1000},
        "steps": {
            "type": "array",
            "maxItems": MAX_STEPS,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "class_type": {"type": "string"},
                    "inputs": {"type": "object"},
                },
                "required": ["id", "class_type", "inputs"],
                "additionalProperties": False,
            },
        },
        "output": REF,
    },
    "required": ["explanation", "steps", "output"],
    "additionalProperties": False,
}
INSTRUCTIONS = """FUZZY PLAN REQUEST. Plan a chain of ComfyUI nodes that does the goal on the input text.
Use only the allowed nodes below, with their exact input names and allowed values.
Each input is either a literal value of the declared type or a reference:
{"from": "$input"} for the input text, or {"from": "<earlier step id>", "output": <output index>}.
Give every required input. Step ids are short lowercase names; steps may only reference earlier steps.
"output" is the reference whose value (text) is the result. If the input already is the result, use {"from": "$input"}.
Say in "explanation" what the chain does. Do not invent nodes or inputs."""


def node_schema(class_type: str) -> dict[str, Any] | None:
    """Live schema of an installed node, in /object_info form."""
    import nodes
    from comfy_api.internal import _ComfyNodeInternal

    node = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if node is None:
        return None
    if issubclass(node, _ComfyNodeInternal):
        return node.GET_NODE_INFO_V1()
    return {"input": node.INPUT_TYPES(), "output": list(node.RETURN_TYPES)}


def _check_literal(name: str, spec: list, value: Any) -> None:
    kind, options = spec[0], (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {})
    choices = (
        kind if isinstance(kind, list) else options.get("options") if kind == "COMBO" else None
    )
    if choices is not None:
        if value not in choices:
            raise ValueError(f"{name}: {value!r} is not one of {choices}")
    elif kind == "STRING":
        if not isinstance(value, str):
            raise ValueError(f"{name}: expected text")
    elif kind == "BOOLEAN":
        if not isinstance(value, bool):
            raise ValueError(f"{name}: expected true/false")
    elif kind in ("INT", "FLOAT"):
        if isinstance(value, bool) or not isinstance(value, int if kind == "INT" else (int, float)):
            raise ValueError(f"{name}: expected {kind.lower()}")
        if ("min" in options and value < options["min"]) or (
            "max" in options and value > options["max"]
        ):
            raise ValueError(f"{name}: {value} is out of range")
    else:
        raise ValueError(f"{name}: {kind} inputs must be connected, not literal")


def validate_plan(plan: dict, allowed: dict[str, dict]) -> None:
    """Raise ValueError unless the plan only uses allowed nodes, correctly typed and ordered."""
    steps = plan.get("steps")
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError(f"a plan has at most {MAX_STEPS} steps")
    outputs: dict[str, list] = {INPUT: ["STRING"]}

    def source_type(ref: Any, where: str) -> str:
        if (
            not isinstance(ref, dict)
            or set(ref) - {"from", "output"}
            or ref.get("from") not in outputs
        ):
            raise ValueError(f"{where}: references unknown or later step {ref!r}")
        index = ref.get("output", 0)
        types = outputs[ref["from"]]
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(types):
            raise ValueError(f"{where}: {ref['from']} has no output {index}")
        return types[index]

    for step in steps:
        sid, class_type, inputs = step.get("id"), step.get("class_type"), step.get("inputs")
        if not isinstance(sid, str) or not STEP_ID.fullmatch(sid) or sid in outputs:
            raise ValueError(f"invalid or duplicate step id {sid!r}")
        if class_type not in allowed:
            raise ValueError(f"{sid}: {class_type!r} is not an allowed node")
        if not isinstance(inputs, dict):
            raise ValueError(f"{sid}: inputs must be an object")
        spec = allowed[class_type]["input"]
        declared = {**spec.get("required", {}), **spec.get("optional", {})}
        for name in spec.get("required", {}):
            if name not in inputs:
                raise ValueError(f"{sid}: missing required input {name!r}")
        for name, value in inputs.items():
            where = f"{sid}.{name}"
            if name not in declared:
                raise ValueError(f"{where}: {class_type} has no input {name!r}")
            if isinstance(value, dict):
                expected, actual = declared[name][0], source_type(value, where)
                if "*" not in (expected, actual) and expected != actual:
                    raise ValueError(f"{where}: cannot connect {actual} to {expected}")
            else:
                _check_literal(where, declared[name], value)
        outputs[sid] = list(allowed[class_type]["output"])
    if source_type(plan.get("output"), "output") not in ("STRING", "*"):
        raise ValueError("output must be text")


def expand_plan(plan: dict, text: str) -> tuple[Any, dict | None]:
    """The result value (or link) and the GraphBuilder graph that computes it."""
    graph, built = GraphBuilder(), {}

    def resolve(value):
        if not isinstance(value, dict):
            return value
        return text if value["from"] == INPUT else built[value["from"]].out(value.get("output", 0))

    for step in plan["steps"]:
        built[step["id"]] = graph.node(
            step["class_type"], **{k: resolve(v) for k, v in step["inputs"].items()}
        )
    result = resolve(plan["output"])
    return result, (graph.finalize() if built else None)


def describe_plan(plan: dict) -> str:
    def show(value):
        if isinstance(value, dict):
            return (
                value["from"]
                if value["from"] == INPUT
                else f"{value['from']}[{value.get('output', 0)}]"
            )
        return json.dumps(value, ensure_ascii=False)[:60]

    lines = [plan.get("explanation", "")[:300]]
    for n, step in enumerate(plan["steps"], 1):
        args = ", ".join(f"{k}={show(v)}" for k, v in step["inputs"].items())
        lines.append(f"{n}. {step['id']} = {step['class_type']}({args})")
    lines.append(f"result = {show(plan['output'])}")
    return "\n".join(lines)


class FuzzyNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowFuzzyNode",
            display_name="Fuzzy · Plan with nodes",
            category="agent_workflow_studio/fuzzy",
            search_aliases=["fuzzy", "plan", "agent", "LLM", "text"],
            description=(
                "Describe a text job; the model plans a chain of allowed installed nodes, which is "
                "validated against their schemas and runs as real nodes. No code is generated."
            ),
            inputs=[
                io.String.Input(
                    "goal",
                    display_name="Goal",
                    multiline=True,
                    default="",
                    tooltip="What to do with the input text, in plain words.",
                ),
                io.String.Input(
                    "text",
                    display_name="Input text",
                    multiline=True,
                    default="",
                    tooltip="The text to process; connect a text node or type it here.",
                ),
                io.String.Input(
                    "allowed_nodes",
                    display_name="Allowed nodes",
                    multiline=True,
                    default=DEFAULT_NODES,
                    tooltip="Comma-separated node class types the plan may use.",
                ),
                PiModelConfigSocket.Input(
                    "pi_config",
                    display_name="Pi model config",
                    optional=True,
                    tooltip="Connect Pi CLI · Model Config to choose the planning model.",
                ),
            ],
            outputs=[
                io.String.Output(display_name="result"),
                io.Dict.Output(display_name="plan"),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            enable_expand=True,
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        # Planning calls the model; never reuse a cached plan implicitly.
        return float("nan")

    @classmethod
    def execute(
        cls, goal: str, text: str, allowed_nodes: str, pi_config: Any = None
    ) -> io.NodeOutput:
        names = [n.strip() for n in allowed_nodes.replace("\n", ",").split(",") if n.strip()]
        allowed = {name: node_schema(name) for name in names}
        missing = [name for name, schema in allowed.items() if schema is None]
        if missing:
            raise ValueError(f"Allowed nodes are not installed: {', '.join(missing)}")
        if not goal.strip():
            raise ValueError("Describe the goal.")
        brief = {
            name: {"inputs": schema["input"], "outputs": schema["output"]}
            for name, schema in allowed.items()
        }
        request = (
            f"{INSTRUCTIONS}\n\nGoal: {goal}\n\nInput text (sample, may be truncated):\n{text[:2000]}"
            f"\n\nAllowed nodes:\n{json.dumps(brief, ensure_ascii=False)}"
        )
        unique_id = node_id_for(cls)
        config = validate_pi_model_config(pi_config) if pi_config is not None else None
        harness = LLMRunHarness(unique_id, "Fuzzy plan")
        started = time.time()
        plan, error = None, None
        try:
            plan = harness.run(complete_json, request, json.dumps(PLAN_SCHEMA), pi_config=config)
            validate_plan(plan, allowed)
        except Exception as exc:  # recorded below, then re-raised unchanged
            error = exc
        recorded = record_run(
            cls,
            "fuzzy_plan",
            "Fuzzy plan",
            unique_id,
            harness,
            started,
            {"goal": goal, "text": text, "allowed_nodes": names},
            config,
            plan,
            error,
        )
        if error is not None:
            raise error
        result, expansion = expand_plan(plan, text)
        summary = f"FUZZY PLAN · VALIDATED · {len(plan['steps'])} step(s)\n{describe_plan(plan)}\n{recorded}"
        return io.NodeOutput(result, plan, ui={"text": (summary,)}, expand=expansion)


__all__ = ["FuzzyNode", "describe_plan", "expand_plan", "validate_plan"]
