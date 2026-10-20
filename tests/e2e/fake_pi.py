#!/usr/bin/env python3
"""Offline stand-in for the Pi CLI in RPC mode.

Only the LLM is faked: tool calls go through the real bridge to the real canvas
and queue. Scripted turns are chosen by keywords in the prompt.
FAKE_PI_ADDONS=0 reports no SoL-Pi/Ponytail add-ons.
"""

import json
import os
import sys
import time
from urllib.request import Request, urlopen

ADDONS = os.environ.get("FAKE_PI_ADDONS", "1") != "0"
# The Structured LLM node starts Pi with only the typed-output extension.
STRUCTURED = any(arg.endswith("pi_structured_output_extension.ts") for arg in sys.argv)
EXTENSIONS = (
    {
        "sol_pi": {"version": "fake", "actionFusion": False, "observationPack": False},
        "ponytail": {"version": "fake", "mode": "ultra"},
    }
    if ADDONS
    else {}
)
provider, model, thinking = "offline-fixture", "no-provider", "off"


def emit(record):
    print(json.dumps(record), flush=True)


def respond(record, data=None):
    emit(
        {
            "type": "response",
            "id": record["id"],
            "success": True,
            **({"data": data} if data else {}),
        }
    )


def say(value):
    emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
    emit(
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": value}}
    )
    emit(
        {
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": value}]},
        }
    )


def tool(action, args):
    call = "fake-" + str(time.monotonic_ns())
    name = "comfy_" + action
    emit({"type": "tool_execution_start", "toolCallId": call, "toolName": name, "args": args})
    request = Request(
        os.environ["COMFY_PI_BRIDGE_URL"],
        data=json.dumps({"action": action, "args": args}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["COMFY_PI_BRIDGE_TOKEN"],
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            result, error = json.load(response)["result"], False
    except (OSError, ValueError, KeyError) as failure:
        result, error = {"error": str(failure)}, True
    result.pop("image_previews", None)
    emit(
        {
            "type": "tool_execution_end",
            "toolCallId": call,
            "toolName": name,
            "isError": error,
            "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
        }
    )
    return result


CONTRACT = {
    "contract": 2,
    "purpose": "Show the text hello world.",
    "requests": [
        {
            "id": "R1",
            "title": "Show hello world",
            "you_said": "build a hello world workflow",
            "we_understood": "When run, the workflow shows hello world.",
            "priority": "must",
        }
    ],
    "examples": [{"id": "E1", "covers": ["R1"], "given": {}, "expect": {"text": "hello world"}}],
    "promises": {
        "inputs": [],
        "outputs": [
            {"name": "text", "kind": "text", "example": "hello world", "from": "greet.text"}
        ],
    },
    "steps": [
        {
            "id": "greet",
            "pattern": "pipes and filters",
            "purpose": "Join hello and world into one greeting.",
            "approach": "fixed",
            "covers": ["R1"],
            "gives": {"text": "STRING"},
            "gate": {
                "says": "The greeting says hello world.",
                "check": {"output": "text", "kind": "contains", "value": "hello world"},
            },
            "nodes": ["PrimitiveString", "StringConcatenate"],
        },
        {
            "id": "show",
            "pattern": "pipes and filters",
            "purpose": "Show the greeting.",
            "approach": "fixed",
            "covers": ["R1"],
            "takes": {"text": "greet.text"},
            "gate": {"says": "The greeting is easy to read."},
            "nodes": ["PreviewAny"],
        },
    ],
    "decisions": [
        {
            "id": "D1",
            "decision": "Use hello world",
            "why": "Which greeting? hello world",
            "by": "user",
        }
    ],
    "not_now": [],
    "open_questions": [],
}


def propose(contract):
    """The contract (intent) first, then its design (HLD): two approvals, as a real agent does it."""
    design = {
        "steps": contract["steps"],
        "outputs": {o["name"]: o["from"] for o in contract["promises"]["outputs"]},
    }
    outputs = [{k: v for k, v in o.items() if k != "from"} for o in contract["promises"]["outputs"]]
    intent = {
        **{k: v for k, v in contract.items() if k != "steps"},
        "promises": {**contract["promises"], "outputs": outputs},
    }
    return tool("contract_propose", {"contract": intent}).get("approved") and tool(
        "design_propose", {"design": design}
    ).get("approved")


def ask(question, options):
    """A select dialog, exactly as Pi's ctx.ui.select emits it; waits for the user's answer."""
    request_id = "fake-ask-" + str(time.monotonic_ns())
    emit(
        {
            "type": "extension_ui_request",
            "id": request_id,
            "method": "select",
            "title": question,
            "options": options,
        }
    )
    while line := sys.stdin.readline():
        record = json.loads(line)
        if record.get("type") == "extension_ui_response" and record.get("id") == request_id:
            return record.get("value")
    raise SystemExit("stdin closed while waiting for an answer")


# Each stage's LLD: its nodes, what each realizes of the step, and its wires.
GREET = {
    "step": "greet",
    "summary": "Build greet: source and concatenate",
    "nodes": {
        "src": {"type": "PrimitiveString", "set": {"value": "hello"}, "realizes": ["does"]},
        "cat": {
            "type": "StringConcatenate",
            "set": {"string_b": " world"},
            "realizes": ["does", "gives text"],
        },
    },
    "wires": [{"from": "src.0", "to": "cat.string_a"}, {"from": "cat.0", "to": "output.text"}],
}
SHOW = {
    "step": "show",
    "summary": "Build show: preview",
    "nodes": {"out": {"type": "PreviewAny", "title": "Result", "realizes": ["takes text", "does"]}},
    "wires": [{"from": "input.text", "to": "out.source"}],
}


def build_and_run():
    tool("read", {})
    tool("catalog", {"class_types": ["PrimitiveString", "StringConcatenate", "PreviewAny"]})
    if (
        ask("Which greeting should the workflow show?", ["hello world", "good morning"])
        != "hello world"
    ):
        raise SystemExit("unexpected answer")
    if not propose(CONTRACT):
        say("FAKE PI · contract not approved.")
        return
    tool("build_step", GREET)
    built = tool("build_step", SHOW)
    # A node built from an LLD is addressed by the id the build returned; layout edits stay allowed.
    out = (built.get("nodes") or {}).get("out")
    moved = tool(
        "edit",
        {"operations": [{"op": "move", "stage": "show", "node": out, "position": [760, 100]}]},
    )
    if "error" in moved:
        raise SystemExit("built node did not resolve: " + moved["error"])
    run = tool("run", {"reason": "build and verify"})
    if not run.get("prompt_id"):
        raise SystemExit("run returned no prompt_id")
    for _ in range(60):
        result = tool("results", {})
        if result.get("prompt_id") != run["prompt_id"]:
            raise SystemExit("results tracked a different run")
        if (result.get("status") or {}).get("completed"):
            break
        time.sleep(0.5)
    gates = tool("contract_gates", {})["gates"]
    summary = ", ".join(f"{gate['step']}={gate['status']}" for gate in gates)
    progress = tool("contract_status", {})["progress"]
    say(
        f"FAKE PI · built source → concatenate → preview and ran it. Gates: {summary}. Progress: {progress}."
    )


def structured_answer(record):
    """Answer like a model filling the typed-output tool once."""
    respond(record)
    emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
    answer = {"answer": "fake answer"}
    if "FUZZY PLAN REQUEST" in record["message"]:
        answer = {
            "explanation": "FAKE PI · uppercase the input",
            "steps": [
                {
                    "id": "upper",
                    "class_type": "CaseConverter",
                    "inputs": {"string": {"from": "$input"}, "mode": "UPPERCASE"},
                }
            ],
            "output": {"from": "upper", "output": 0},
        }
    call = {"id": "fake-call", "name": "agent_workflow_submit_json", "arguments": answer}
    emit(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_start", "toolName": call["name"]},
        }
    )
    emit(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_end", "toolCall": call},
        }
    )
    emit(
        {
            "type": "message_end",
            "message": {"role": "assistant", "stopReason": "toolUse", "content": []},
        }
    )


TOOL_CONTRACT = {
    "contract": 2,
    "purpose": "Shout the greeting with an agent tool.",
    "requests": [
        {
            "id": "R1",
            "title": "Shout with a tool",
            "you_said": "make it loud with a tool",
            "we_understood": "When run, the tool uppercases the text.",
            "priority": "must",
        }
    ],
    "examples": [
        {"id": "E1", "covers": ["R1"], "given": {"text": "hello"}, "expect": {"text": "HELLO!"}}
    ],
    "promises": {
        "inputs": [],
        "outputs": [{"name": "text", "kind": "text", "from": "shout.text"}],
    },
    "steps": [
        {
            "id": "shout",
            "pattern": "pipes and filters",
            "purpose": "Uppercase the text with an approved tool.",
            "approach": "fixed",
            "covers": ["R1"],
            "gives": {"text": "STRING"},
            "gate": {
                "says": "The text is shouted.",
                "check": {"output": "text", "kind": "contains", "value": "HELLO!"},
            },
        }
    ],
    "decisions": [],
    "not_now": [],
    "open_questions": [],
}
SHOUT = "def run(inputs):\n    return {'text': inputs['text'].upper() + '!'}\n"


def tool_workflow():
    if not propose(TOOL_CONTRACT):
        return say("FAKE PI · contract not approved.")
    import base64

    tool(
        "workspace_write",
        {
            "path": "tools/shout.py",
            "data": base64.b64encode(SHOUT.encode()).decode(),
            "expected_sha256": None,
        },
    )
    ports = [{"name": "text", "kind": "text"}]
    approved = tool(
        "tool_propose",
        {
            "name": "shout",
            "description": "Uppercase and add an exclamation mark.",
            "inputs": ports,
            "outputs": ports,
        },
    )
    if not approved.get("approved"):
        return say("FAKE PI · tool not approved.")
    tool(
        "build_step",
        {
            "step": "shout",
            "summary": "Agent tool shout",
            "nodes": {
                "shout": {
                    "type": "AgentWorkflowAgentTool",
                    "set": {"tool": "shout", "text": "hello"},
                    "realizes": ["does", "gives text"],
                }
            },
            "wires": [{"from": "shout.1", "to": "output.text"}],
        },
    )
    tool("run", {"reason": "run the tool"})
    tool("results", {})
    gates = tool("contract_gates", {})["gates"]
    say(
        "FAKE PI · tool workflow ran. Gates: "
        + ", ".join(f"{g['step']}={g['status']}" for g in gates)
        + "."
    )


def comfy_tools_notice():
    return {
        "type": "comfy_tools",
        "schema_only_prompt": False,
        "skills": {
            "names": [
                "comfyui-connector-management",
                "comfyui-node-creation",
                "comfyui-node-organization",
                "comfyui-workflow-debugging",
                "comfyui-workflow-contracts",
                "comfyui-design-patterns",
            ],
            "default_loaded": True,
        },
        "workspace": {
            "workflow_id": os.environ["COMFY_PI_WORKFLOW_ID"],
            "graph_id": os.environ["COMFY_PI_WORKFLOW_GRAPH_ID"],
            "root": os.environ["COMFY_PI_WORKSPACE_DIR"],
            "file_tools_scoped": True,
            "shell_enabled": False,
        },
        "extensions": EXTENSIONS,
        "active": [
            "comfy_read_workflow",
            "comfy_node_catalog",
            "comfy_edit_workflow",
            "comfy_run_workflow",
            "comfy_execution_results",
        ],
    }


while line := sys.stdin.readline():
    record = json.loads(line)
    kind = record["type"]
    if kind == "set_model":
        provider, model = record["provider"], record["modelId"]
        respond(record)
    elif kind == "set_thinking_level":
        thinking = record["level"]
        respond(record)
    elif kind == "get_state":
        respond(record, {"model": {"provider": provider, "id": model}, "thinkingLevel": thinking})
    elif kind == "get_available_models":
        respond(record, {"models": [{"provider": provider, "id": model, "name": model}]})
    elif kind == "get_commands":
        names = [{"name": "comfy"}] + ([{"name": "ponytail"}] if ADDONS else [])
        respond(record, {"commands": names})
    elif kind == "prompt" and record["message"] == "/ponytail ultra":
        respond(record, {"disposition": "handled"})
    elif kind == "prompt" and record["message"].startswith("/comfy"):
        emit(
            {
                "type": "extension_ui_request",
                "id": "tools",
                "method": "notify",
                "message": json.dumps(comfy_tools_notice()),
            }
        )
        respond(record, {"disposition": "handled"})
    elif kind == "prompt" and STRUCTURED:
        structured_answer(record)
    elif kind == "prompt":
        emit({"type": "agent_start"})
        if "tool" in record["message"].lower():
            tool_workflow()
        elif "build" in record["message"].lower():
            build_and_run()
        else:
            say("FAKE PI · no scripted action for this message.")
        emit({"type": "agent_end", "messages": []})
    elif kind == "abort":
        emit({"type": "agent_end", "messages": []})
    elif "id" in record:
        respond(record)
