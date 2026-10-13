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
        "ponytail": {"version": "fake", "mode": "full"},
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


BUILD = [
    {"op": "add", "key": "src", "class_type": "PrimitiveString", "position": [50, 100]},
    {"op": "widget", "node": "src", "name": "value", "value": "hello"},
    {"op": "add", "key": "cat", "class_type": "StringConcatenate", "position": [350, 100]},
    {"op": "widget", "node": "cat", "name": "string_b", "value": " world"},
    {"op": "add", "key": "out", "class_type": "PreviewAny", "position": [700, 100]},
    {"op": "connect", "from": "src", "output": 0, "to": "cat", "input": "string_a"},
    {"op": "connect", "from": "cat", "output": 0, "to": "out", "input": "source"},
]


def build_and_run():
    tool("read", {})
    tool("catalog", {"class_types": ["PrimitiveString", "StringConcatenate", "PreviewAny"]})
    tool(
        "edit", {"operations": BUILD, "summary": "Build hello world: source, concatenate, preview"}
    )
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
    say("FAKE PI · built source → concatenate → preview and ran it.")


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


for line in sys.stdin:
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
    elif kind == "prompt" and record["message"] == "/ponytail full":
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
        if "build" in record["message"].lower():
            build_and_run()
        else:
            say("FAKE PI · no scripted action for this message.")
        emit({"type": "agent_end", "messages": []})
    elif kind == "abort":
        emit({"type": "agent_end", "messages": []})
    elif "id" in record:
        respond(record)
