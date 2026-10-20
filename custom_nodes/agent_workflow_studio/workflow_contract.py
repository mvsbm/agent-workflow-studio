"""The workflow contract: one per workflow, stored natively in the workflow itself
(native_layers.py: intent in extra, each step on its stage node), versioned in its project history.

The joined contract (purpose, requests, examples, promises, steps, decisions, not-now, open
questions) is validated against contract.schema.json, shared with the agent's tool and editors. Step status is never stored: it is
derived from evidence (canvas, runs, gate checks) by folding events through one
transition table, so it cannot drift from reality.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from . import api_review

SCHEMA_PATH = Path(__file__).with_name("contract.schema.json")
SCHEMA = json.loads(SCHEMA_PATH.read_text())
_validator = Draft202012Validator(SCHEMA)
_intent_validator = None

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
    "verified": "",  # the badge says it; only a turn for the user is worth a line
    "failing": "Fix the step's nodes, run again and re-check gates.",
}


def title_of(request: dict) -> str:
    """A request's short name (IEEE 29148: an id is never shown alone). Contracts from before titles
    get one from their restatement: "When records are cleaned, each system gets …" → "each system gets …"."""
    if request.get("title"):
        return request["title"]
    text = re.sub(r"^When [^,]*, (the workflow )?", "", request.get("we_understood") or "").rstrip(
        "."
    )
    text = text[:1].upper() + text[1:]
    return text if len(text) <= 40 else text[:39].rstrip() + "…"


def raise_all(problems: list[str]) -> None:
    """One ValueError naming every problem: one as it is, several numbered."""
    if len(problems) == 1:
        raise ValueError(problems[0])
    if problems:
        raise ValueError(
            f"{len(problems)} problems to fix together: "
            + " ".join(f"{i}) {p}" for i, p in enumerate(problems, 1))
        )


def validate_contract(contract: object, *, intent_only: bool = False) -> dict:
    """Raise ValueError unless the contract matches the schema and is internally consistent.
    intent_only: the contract before its design (no steps yet), so nothing covers its requests yet."""
    for r in contract.get("requests") or [] if isinstance(contract, dict) else []:
        if isinstance(r, dict) and not r.get("title"):
            r["title"] = title_of(
                r
            )  # stored before titles existed; a new contract must give its own
    validator = _validator
    if intent_only:  # checked against the intent schema (no steps); the steps list stays empty here
        global _intent_validator
        _intent_validator = _intent_validator or Draft202012Validator(agent_schemas()["intent"])
        validator = _intent_validator
    shown = {k: v for k, v in contract.items() if k != "steps"} if intent_only else contract
    errors = sorted(validator.iter_errors(shown), key=lambda e: list(e.absolute_path))
    if errors:
        # Every shape problem at once (not one per attempt), each with where it is.
        raise_all(
            [
                f"Invalid contract at {'/'.join(str(p) for p in e.absolute_path) or 'contract'}: {e.message[:200]}"
                for e in errors[:10]
            ]
        )
    problems: list[str] = []
    for section in ("requests", "examples", "steps", "decisions"):
        ids = [item["id"] for item in contract[section]]
        if twice := sorted({i for i in ids if ids.count(i) > 1}):
            shared = (
                " (the contract's decisions and the design's share one numbering: continue after the "
                "contract's)"
                if section == "decisions"
                else ""
            )
            problems.append(
                f"Each entry in {section} needs a unique id: {', '.join(twice)} used twice{shared}."
            )
    requests = {r["id"]: r for r in contract["requests"]}
    for item in contract["examples"] + contract["steps"]:
        unknown = set(item["covers"]) - requests.keys()
        if unknown:
            problems.append(f"{item['id']} covers unknown request(s): {', '.join(sorted(unknown))}")
    if intent_only:
        raise_all(problems)
        return contract
    covered = {r for step in contract["steps"] for r in step["covers"]}
    missing = [
        r["id"] for r in contract["requests"] if r["priority"] != "could" and r["id"] not in covered
    ]
    if missing:
        problems.append(
            f"No step covers request(s) {', '.join(missing)}; add a step or move them to not_now."
        )
    gives = {}
    for step in contract["steps"]:
        check = step["gate"].get("check")
        if check and check["kind"] != "non_empty" and "value" not in check:
            problems.append(f"Step {step['id']}: a {check['kind']} check needs a value.")
        if check and "output" in check and check["output"] not in step.get("gives", {}):
            problems.append(
                f"Step {step['id']}: the check is on {check['output']}, which the step does not give."
            )
        # Stages are wired from earlier steps only, so the skeleton is a graph that can run.
        for port, source in step.get("takes", {}).items():
            if source.startswith("input."):
                if source[6:] not in {i["name"] for i in contract["promises"]["inputs"]}:
                    problems.append(
                        f"Step {step['id']} takes {port} from {source}, which is not a promised input."
                    )
            elif "." in source:
                src, out = source.split(".")
                if out not in gives.get(src, {}):
                    problems.append(
                        f"Step {step['id']} takes {port} from {source}, which no earlier step gives."
                    )
        gives[step["id"]] = step.get("gives", {})
    for out in contract["promises"]["outputs"]:
        if "from" in out:
            src, port = out["from"].split(".")
            if port not in gives.get(src, {}):
                problems.append(
                    f"Promised output {out['name']} comes from {out['from']}, which no step gives."
                )
    entities = contract.get("data") or {}
    for name, entity in entities.items():
        for field, target in (entity.get("refs") or {}).items():
            if target not in entities:
                problems.append(f"Entity {name}: {field} refers to unknown entity {target}.")
    for q in contract["open_questions"]:
        if isinstance(q, dict) and q["step"] not in gives:
            problems.append(f"Open question “{q['question']}” waits on unknown step {q['step']}.")
    raise_all(problems)
    return contract


DESIGN_KEYS = ("steps", "data", "loop")


def validate_intent(intent: object) -> dict:
    """The contract alone: what the user wants (purpose, requests, examples, promises, not-now,
    their decisions). Steps, data and where each promised output comes from belong to the design."""
    if not isinstance(intent, dict):
        raise ValueError("The contract must be an object.")
    problems = []  # every problem at once: a proposal is fixed in one retry, not one per problem
    if found := [k for k in DESIGN_KEYS if k in intent]:
        problems.append(
            f"The contract is intent only; {', '.join(found)} belong to the design: propose them with "
            "comfy_propose_design once the contract is approved."
        )
    outputs = (intent.get("promises") or {}).get("outputs", [])
    if any(isinstance(o, dict) and "from" in o for o in outputs):
        problems.append(
            "Which step delivers a promised output is design: give it in comfy_propose_design (outputs)."
        )
    if str(intent.get("contract")) == "2":
        intent["contract"] = 2  # some models send the marker as text
    if untitled := [
        str((r or {}).get("id"))
        for r in intent.get("requests") or []
        if not isinstance(r, dict) or not r.get("title")
    ]:
        problems.append(
            f"Give each request a short title (a few words the user will see on the canvas): {', '.join(untitled)}."
        )
    # The rest checked as if those were fixed, so their problems come in the same answer.
    shown = {k: v for k, v in intent.items() if k not in DESIGN_KEYS}
    if isinstance(intent.get("promises"), dict) and isinstance(outputs, list):
        bare = [
            {k: v for k, v in o.items() if k != "from"} if isinstance(o, dict) else o
            for o in outputs
        ]
        shown["promises"] = {**intent["promises"], "outputs": bare}
    try:
        validate_contract({**shown, "steps": []}, intent_only=True)
    except ValueError as error:
        problems.append(str(error))
    if blocking := [q for q in intent.get("open_questions") or [] if isinstance(q, str)]:
        problems.append(
            "Resolve the open questions with the user (comfy_ask_user) before asking for approval: "
            + "; ".join(blocking)
        )
    raise_all(problems)
    return intent


STEP_PORT = "^[a-z][a-z0-9_]{0,31}\\.[a-z][a-z0-9_]{0,31}$"


def agent_schemas() -> dict[str, dict]:
    """The agent tools' schemas (intent, design, build), derived from contract.schema.json, the one
    source; saved as <name>.schema.json for both agents' tool loaders (test_workflow_contract checks)."""
    import copy

    base = copy.deepcopy(SCHEMA)
    defs = base.pop("$defs", {})
    for key in ("$schema", "$id", "title"):
        base.pop(key, None)
    full = json.loads(
        json.dumps(base).replace('{"$ref": "#/$defs/port"}', json.dumps(defs["port"]))
    )
    full["properties"]["contract"] = {"enum": [2, "2"]}  # some models send the marker as text
    intent = copy.deepcopy(full)
    intent["description"] = (
        "The contract: what the user wants (purpose, requests, examples, promised inputs and outputs, "
        "their decisions). How it is done (steps, data, the loop) is the design: comfy_propose_design."
    )
    for key in DESIGN_KEYS:
        intent["properties"].pop(key, None)
    intent["required"] = [r for r in intent["required"] if r not in DESIGN_KEYS]
    intent["properties"]["promises"]["properties"]["outputs"]["items"]["properties"].pop(
        "from", None
    )
    design = {
        "type": "object",
        "properties": {
            **{k: full["properties"][k] for k in ("steps", "data", "decisions", "loop")},
            "outputs": {
                "type": "object",
                "description": 'Which step port delivers each promised output: {"<output name>": "<step>.<port>"}.',
                "additionalProperties": {"type": "string", "pattern": STEP_PORT},
            },
        },
        "required": ["steps", "outputs"],
        "additionalProperties": False,
    }
    key = "^[a-z][a-z0-9_]{0,31}$"
    build = {
        "type": "object",
        "properties": {
            "step": {
                "type": "string",
                "pattern": key,
                "description": "The step (stage) this LLD builds.",
            },
            "nodes": {
                "type": "object",
                "maxProperties": 40,
                "propertyNames": {"pattern": key},
                "description": "The stage's nodes by key.",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Node class from comfy_node_catalog.",
                        },
                        "title": {"type": "string", "maxLength": 120},
                        "set": {"type": "object", "description": "Widget name → value."},
                        "realizes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                            "description": 'The HLD clauses it realizes: "takes <port>", "does", "gives <port>", "why", "decision <id>".',
                        },
                    },
                    "required": ["type", "realizes"],
                    "additionalProperties": False,
                },
            },
            "wires": {
                "type": "array",
                "maxItems": 120,
                "description": 'Each {"from": "input.<port>" | "<node>.<socket>", "to": "<node>.<socket>" | "output.<port>"}.',
                "items": {
                    "type": "object",
                    "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
                    "required": ["from", "to"],
                    "additionalProperties": False,
                },
            },
            "summary": {
                "type": "string",
                "maxLength": 200,
                "description": "One line: what this build does.",
            },
        },
        "required": ["step", "nodes", "wires"],
        "additionalProperties": False,
    }
    return {"intent": intent, "design": design, "build": build}


def write_agent_schemas() -> None:
    for name, schema in agent_schemas().items():
        SCHEMA_PATH.with_name(f"{name}.schema.json").write_text(
            json.dumps(schema, indent=1, ensure_ascii=False) + "\n"
        )


def join(intent: dict, design: dict) -> dict:
    """Contract + design (HLD) as one: the shape the review, the skeleton and the views work on."""
    sources = design.get("outputs") or {}
    promises = {
        **intent["promises"],
        "outputs": [
            {**o, **({"from": sources[o["name"]]} if o["name"] in sources else {})}
            for o in intent["promises"]["outputs"]
        ],
    }
    return {
        **intent,
        "promises": promises,
        "steps": design.get("steps") or [],
        "decisions": [*intent.get("decisions", []), *design.get("decisions", [])],
        **{k: design[k] for k in ("data", "loop") if k in design},
    }


def validate_for_approval(contract: object) -> dict:
    if isinstance(contract, dict) and str(contract.get("contract")) == "2":
        # The format marker is the studio's business; some models send it as the text "2".
        contract["contract"] = 2
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


def present(contract: dict | None) -> dict | None:
    """The contract without the steps whose stage is gone (native_layers marks them missing);
    those are reported as drift instead."""
    if contract is None:
        return None
    return {**contract, "steps": [s for s in contract["steps"] if not s.get("missing")]}


def derive_states(
    contract: dict, nodes: list[dict], gate_results: dict[str, str], open_: dict | None = None
) -> list[dict]:
    """Each step's state from evidence, by folding canonical events through TRANSITIONS.

    nodes: canvas nodes as {"step": the stage it is in, "fuzzy": bool, "scaffold": bool};
    gate_results: step -> status of its Check nodes on the latest run of exactly this graph
    ("passed", "failed", ...); open_: step -> items waiting for the user's review there.
    A stage holding only its Stub and Checks is still planned.
    """
    result, designs = [], {d["id"]: d["sentence"] for d in hld(contract)}
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
                "design": designs[step["id"]],
                "state": state,
                "nodes": len(mine),
                "next": f"Waiting on your answer: {'; '.join(questions)}"
                if questions
                else f"Your turn: {n:,} open in the Review tab; decide some, then run again."
                if state == "verified" and (n := (open_ or {}).get(step["id"]))
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
            **{f"gives {k}": v for k, v, *_ in stage.get("gives", [])},
        }
        for port, kind in wanted.items():
            if port in have and have[port] != kind and "*" not in (have[port], kind):
                drift.append(f"{stage['title']} {port} as {have[port]}; the contract says {kind}.")
        # Data drift: a declared entity field that the last run did not carry.
        for name, _kind, *seen in stage.get("gives", []):
            fields = (contract.get("data") or {}).get(name, {}).get("fields") or []
            if seen and seen[0] and (missing := [f for f in fields if f not in seen[0]]):
                drift.append(
                    f"{stage['title']} gives {name} without {', '.join(missing)} (declared in the design)."
                )
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


def report(contract: dict, evidence: dict, acceptance: dict | None = None) -> dict:
    """Where the work stands, from browser evidence: one function for the agent and the Contract tab.

    evidence: {"nodes": [{"title", "step", "fuzzy"}], "gate_results": {step: status} from the latest
    run of exactly the current graph (empty if none), "run": {"prompt_id", "current"} | None}.
    A step whose stage is gone (marked missing by native_layers) is drift, not a silent removal.
    """
    missing = [s["id"] for s in contract["steps"] if s.get("missing")]
    contract = present(contract)
    states = derive_states(
        contract, evidence["nodes"], evidence.get("gate_results") or {}, evidence.get("open")
    )
    run = evidence.get("run")
    freshness = (
        "No run of this workflow yet."
        if not run
        else f"Based on the run of {run['at'].replace('T', ' ')} of this exact graph (kept with the workflow)."
        if run.get("current") and run.get("at")
        else "Based on the latest run of this exact graph."
        if run.get("current")
        else "The graph changed since the last run; run it again to check gates."
    )
    from .progress import trace

    drift = alignment(contract, evidence) + [
        f"Step {m} has no stage on the canvas; the design still has it." for m in missing
    ]
    return {
        "trace": trace(contract, evidence, states, drift, acceptance),
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
            f"{st['title']}: its design changed; recheck the nodes inside and run until its Checks pass"
            for st in evidence.get("stages", [])
            if st.get("stale")
        ]
        + [
            f"Answer: {q} (step {step} waits; amend the contract with the answer as a decision)"
            for step, qs in waiting(contract).items()
            for q in qs
        ]
        + todo(evidence),
        "text": render(contract, states),
    }


SHORT = 160  # purpose + uses + why: the words the designer writes (wiring adds the rest)
TYPED = {"TABLE", "PAIRS", "DECISIONS", "CUSTOMERS"}  # data hand-offs that deserve a named entity


def stage_numbers(contract: dict) -> dict[str, int]:
    """Each step's number: one more than the deepest step it takes from (so independent lanes
    share a number); steps declaring no inputs run in order. Mirrors stageNumbers in pi_canvas.js."""
    number: dict[str, int] = {}
    steps = contract["steps"]
    for i, step in enumerate(steps):
        takes = list(step.get("takes", {}).values())
        sources = [
            number.get(t.split(".")[0], 0) for t in takes if "." in t and not t.startswith("input.")
        ]
        number[step["id"]] = (
            (max(sources) + 1)
            if sources
            else 1
            if takes
            else (number[steps[i - 1]["id"]] + 1 if i else 1)
        )
    return number


def title(step_id: str, number: int) -> str:
    return f"{number} · {step_id.replace('_', ' ').capitalize()}"


def hld(contract: dict) -> list[dict]:
    """The high-level design: one sentence per step, derived from the contract's steps.
    takes → does, using → gives → to, because."""
    numbers, steps = stage_numbers(contract), contract["steps"]
    titles = {s["id"]: title(s["id"], numbers[s["id"]]) for s in steps}
    out = []
    for step in steps:
        takes = [
            f"{port} (your input)"
            if src.startswith("input.")
            else f"{port} from {titles.get(src.split('.')[0], src)}"
            if "." in src
            else f"{port} ({src})"
            for port, src in step.get("takes", {}).items()
        ]
        to = [
            titles[s["id"]]
            for s in steps
            if any(v.startswith(f"{step['id']}.") for v in s.get("takes", {}).values())
        ]
        to += [
            f"you (output {o['name']})"
            for o in contract["promises"]["outputs"]
            if o.get("from", "").startswith(f"{step['id']}.")
        ]
        parts = [
            f"takes {', '.join(takes)}" if takes else "",
            step["purpose"].rstrip(". ") + (f" using {step['uses']}" if step.get("uses") else ""),
            "gives " + ", ".join(f"{k} ({v})" for k, v in step.get("gives", {}).items())
            if step.get("gives")
            else "",
            f"to {', '.join(to)}" if to else "",
        ]
        sentence = " → ".join(p for p in parts if p) + (
            f", because {step['why']}" if step.get("why") else ""
        )
        out.append(
            {
                "id": step["id"],
                "title": titles[step["id"]],
                "sentence": sentence,
                "description": black_box(step, contract, takes),
                "pattern": step.get("pattern", ""),
            }
        )
    return out


def black_box(step: dict, contract: dict, takes: list[str]) -> str:
    """The stage seen from outside (arc42 building-block black box): what it does, its interface,
    the requests it serves in the user's words, and how it is verified. Shown as its native description."""
    requests = {r["id"]: r for r in contract["requests"]}
    lines = [
        f"Does: {step['purpose'].rstrip('. ')}"
        + (f" using {step['uses']}" if step.get("uses") else "")
    ]
    if takes:
        lines.append(f"Takes: {', '.join(takes)}")
    if step.get("gives"):
        lines.append("Gives: " + ", ".join(f"{k} ({v})" for k, v in step["gives"].items()))
    for rid in step.get("covers", []):
        if r := requests.get(rid):
            lines.append(f"Serves {rid} · {title_of(r)}: you said “{r['you_said']}”")
    if (step.get("gate") or {}).get("says"):
        lines.append(f"Verified when: {step['gate']['says']}")
    if step.get("why"):
        lines.append(f"Because: {step['why']}")
    return "\n".join(lines)


# Review items a design must pass before the user sees it: they keep the graph, the data model
# and the API consistent (the rest are advice the user weighs).
BLOCKING = (
    "API:",  # the API design standard (api_review): every rule blocks
    "Every step's check tests that its data is right",
    "Every step names its design pattern",
    "Every hand-off between steps is a named data entity",
    "The API is defined: every input is used, every output comes from a step",
    "Every step reads as one short sentence",
)


def person(step: dict) -> bool:
    """A step a person does: marked so in the design (or, earlier, designed with Your review decisions)."""
    return step.get("person") is True or "AgentWorkflowReviewDecisions" in (step.get("nodes") or [])


def shallow(check: dict) -> bool:
    """A check that only shows data exists (non-empty, at least one row): it cannot show it is right."""
    expect = str(check.get("expect", check.get("value")) or "")
    return check.get("kind") == "non_empty" or (
        check.get("kind") == "rows_at_least" and expect in ("", "0", "1")
    )


def blocking(contract: dict) -> list[str]:
    """The review items that stop a proposal, with what to change."""
    return [item for ok, item in review(contract) if not ok and item.startswith(BLOCKING)]


def review(contract: dict) -> list[tuple[bool, str]]:
    """The design review checklist, from the contract alone: what a good design has before building."""
    steps, promises, data = contract["steps"], contract["promises"], contract.get("data") or {}
    consumed = {v for s in steps for v in s.get("takes", {}).values()} | {
        o.get("from") for o in promises["outputs"]
    }
    handoffs = [
        f"{s['id']}.{k}"
        for s in steps
        for k, v in s.get("gives", {}).items()
        if v in TYPED and f"{s['id']}.{k}" in consumed
    ]
    musts = {r["id"] for r in contract["requests"] if r["priority"] == "must"}
    examples = {r for e in contract["examples"] for r in e["covers"]}
    used_inputs = {
        v[6:] for s in steps for v in s.get("takes", {}).values() if v.startswith("input.")
    }
    # Failing items name what to fix, so a retry changes the right step.
    wordy = [
        f"{s['id']} {n}"
        for s in steps
        if (n := sum(len(s.get(k, "")) for k in ("purpose", "uses", "why"))) > SHORT
    ]
    unnamed = [h for h in handoffs if h.split(".")[1] not in data]
    unpatterned = [s["id"] for s in steps if not s.get("pattern")]
    existence = [s["id"] for s in steps if shallow(s["gate"].get("check") or {})]

    def naming(items: list[str], what: str) -> str:
        return f" — {what}: {', '.join(items)}" if items else ""

    return [
        (
            all(s.get("gives") and "*" not in s["gives"].values() for s in steps),
            "Every step gives typed data",
        ),
        (
            # A step a person does (Your review decisions) is proven by the queue reaching them.
            all((s["gate"].get("check") or {}).get("output") or person(s) for s in steps),
            "Every step's result is checked on its data",
        ),
        (
            not existence,
            "Every step's check tests that its data is right, not only that it exists (e.g. rows "
            "at least the expected count, contains a value it must hold)"
            + naming(existence, "only checks existence in"),
        ),
        (all(s.get("uses") and s.get("why") for s in steps), "Every step says how and why"),
        (
            not unpatterned,
            "Every step names its design pattern" + naming(unpatterned, "missing in"),
        ),
        (
            not wordy,
            f"Every step reads as one short sentence (purpose, uses and why ≤ {SHORT} characters together)"
            + naming(wordy, "too long, characters now"),
        ),
        (
            not unnamed,
            "Every hand-off between steps is a named data entity"
            + naming(unnamed, "add to data the entity for"),
        ),
        (
            all(o.get("from") for o in promises["outputs"])
            # An input that is an existing node's widget (node + widget) is used where that node is.
            and used_inputs >= {i["name"] for i in promises["inputs"] if i.get("node") is None},
            "The API is defined: every input is used, every output comes from a step",
        ),
        (musts <= examples, "Every must-request has an example (it becomes an acceptance test)"),
        (
            all(s["gate"].get("check") for s in steps if s["approach"] != "fixed"),
            "AI steps sit behind a check",
        ),
        *api_review.contract_rules(contract),
        *api_review.design_rules(contract),
    ]


def wiring(contract: dict) -> dict:
    """The top-level wires the approved design allows: step to step, and promised input to step."""
    links, inputs = [], []
    for step in contract["steps"]:
        for port, source in step.get("takes", {}).items():
            if source.startswith("input."):
                inputs.append([source[6:], step["id"], port])
            elif "." in source:
                links.append([*source.split("."), step["id"], port])
    return {"links": links, "inputs": inputs}


def render(contract: dict, states: list[dict] | None = None) -> str:
    """Plain-language view: what a non-technical reader approves; technical bits last."""
    approach = {"fixed": "fixed steps", "ai_planned": "AI-planned", "ai_judgment": "AI judgment"}
    status = {s["step"]: s["state"] for s in states or []}
    lines = [f"PURPOSE: {contract['purpose']}", "", "WHAT YOU ASKED FOR:"]
    for r in contract["requests"]:
        lines += [
            f"  {r['id']} · {title_of(r)} ({r['priority']}) you said: “{r['you_said']}”",
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
    if not contract["steps"]:  # the contract alone: intent, before its design
        return "\n".join(lines + _closing(contract))
    lines += ["", "DESIGN (one sentence per step; each step becomes a stage on the canvas):"]
    designs = {d["id"]: d for d in hld(contract)}
    for step in contract["steps"]:
        d, state = designs[step["id"]], f" — {status[step['id']]}" if step["id"] in status else ""
        lines.append(
            f"  {d['title']}: {d['sentence']} [{approach[step['approach']]}"
            + (f"; {d['pattern']}" if d["pattern"] else "")
            + f"] covers {', '.join(step['covers'])}{state}"
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
    if contract.get("data"):
        lines += ["", "DATA (what flows between steps):"]
        for name, e in contract["data"].items():
            key = f" key {', '.join(e['key'])}" if e.get("key") else ""
            refs = "".join(f" · {f} → {t}" for f, t in (e.get("refs") or {}).items())
            lines.append(f"  • {name}: {e['description']}{key}{refs}")
    lines += ["", "DESIGN REVIEW:"] + [
        f"  {'✓' if ok else '✗'} {item}" for ok, item in review(contract)
    ]
    return "\n".join(lines + _closing(contract))


def _closing(contract: dict) -> list[str]:
    """Decisions, not-now and open questions: the end of every rendering."""
    lines = []
    for label, items in (
        (
            "DECISIONS",
            [
                f"{d['id']} {d['decision']} — why: {d['why']}"
                + (f" (instead of {d['instead_of']})" if d.get("instead_of") else "")
                + (f" · pattern: {d['pattern']}" if d.get("pattern") else "")
                + (f" · principle: {d['principle']}" if d.get("principle") else "")
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
    return lines


__all__ = [
    "title_of",
    "NEXT_ACTIONS",
    "SCHEMA",
    "TRANSITIONS",
    "derive_states",
    "render",
    "alignment",
    "blocking",
    "hld",
    "wiring",
    "review",
    "stage_numbers",
    "report",
    "join",
    "present",
    "validate_intent",
    "validate_contract",
    "validate_for_approval",
]
