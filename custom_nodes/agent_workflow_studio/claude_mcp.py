"""The studio's tools for Claude Code, as a stdio MCP server (standard library only).

Claude Code starts this with the chat session's environment: every tool call is POSTed to that
session's bridge (COMFY_PI_BRIDGE_URL + bearer token), exactly as Pi's extension does, so the
server enforces the same contract gate, approvals and workspace confinement. The tools come from
agent_tools.json, shared with Pi; read/edit/write work on the workflow's workspace only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAVED_NOTE = (
    "Saved a workspace file only, not a ComfyUI node execution output. This does not run the "
    "workflow or prove dataset generation. The user can download it from Workflow files."
)
CONFINED = "Confined to this workflow's persistent workspace; use relative paths. "
FILE_TOOLS = [
    {
        "name": "read",
        "description": CONFINED
        + "Read a text file, with line numbers. Read a file before editing it.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 1, "description": "First line (1-based)."},
                "limit": {"type": "integer", "minimum": 1, "description": "How many lines."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit",
        "description": CONFINED
        + "Replace old_string with new_string in a file you read (unique unless replace_all); "
        "refused if the file changed since you read it.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write",
        "description": CONFINED
        + "Create or replace a file; replacing an existing one asks the user first.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
]


def contract_schema() -> dict:
    """The contract's JSON Schema with its $ref inlined, as Pi's extension gives it."""
    schema = json.loads((HERE / "contract.schema.json").read_text(encoding="utf-8"))
    defs = schema.pop("$defs", {})
    for key in ("$schema", "$id", "title"):
        schema.pop(key, None)
    schema["properties"]["contract"] = {"enum": [2, "2"]}  # some models send the marker as text
    text = json.dumps(schema).replace('{"$ref": "#/$defs/port"}', json.dumps(defs.get("port")))
    return json.loads(text)


def tool_list() -> list[dict]:
    shared = json.loads((HERE / "agent_tools.json").read_text(encoding="utf-8"))
    contract = json.dumps(contract_schema())
    tools = []
    for tool in [*shared, *FILE_TOOLS]:
        # The agent schemas (intent, design, build) are generated already inlined by workflow_contract.
        schema = re.sub(
            r'\{"\$ref": "(\w+)\.schema\.json"\}',
            lambda m: (
                contract
                if m[1] == "contract"
                else (HERE / f"{m[1]}.schema.json").read_text(encoding="utf-8")
            ),
            json.dumps(tool["parameters"]),
        )
        tools.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": json.loads(schema),
            }
        )
    return tools


def bridge(action: str, args: dict):
    url, token = os.environ.get("COMFY_PI_BRIDGE_URL"), os.environ.get("COMFY_PI_BRIDGE_TOKEN")
    if not url or not token:
        raise RuntimeError("This tool requires the studio's chat session.")
    request = urllib.request.Request(
        url,
        data=json.dumps({"action": action, "args": args}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        # No time limit: an approval waits for the user's click.
        with urllib.request.urlopen(request, timeout=None) as response:  # noqa: S310 (loopback bridge)
            return json.loads(response.read())["result"]
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error")
        except ValueError:
            message = None
        raise RuntimeError(message or "ComfyUI action failed.") from None


class Workspace:
    """read/edit/write on the workspace bridge, with Pi's rule: edit only what you read, unchanged."""

    def __init__(self, call=bridge):
        self.call, self.hashes = call, {}

    def read(self, path: str, offset: int = 1, limit: int = 2000) -> str:
        file = self.call("workspace_read", {"path": path})
        data = base64.b64decode(file["data"])
        if b"\0" in data[:8192]:
            raise RuntimeError("Binary file: only text files can be read.")
        self.hashes[path] = file["sha256"]
        lines = data.decode("utf-8", errors="replace").splitlines()
        shown = lines[offset - 1 : offset - 1 + limit]
        text = "\n".join(f"{offset + i:6}\t{line}" for i, line in enumerate(shown))
        more = len(lines) - (offset - 1 + len(shown))
        return text + (f"\n… {more} more lines (use offset)" if more > 0 else "")

    def edit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        expected = self.hashes.get(path)
        if not expected:
            raise RuntimeError("Read the workspace file before editing.")
        file = self.call("workspace_read", {"path": path})
        if file["sha256"] != expected:
            raise RuntimeError("The file changed since you read it; read it again.")
        text = base64.b64decode(file["data"]).decode("utf-8")
        count = text.count(old_string)
        if count == 0 or (count > 1 and not replace_all):
            raise RuntimeError(
                "old_string not found."
                if not count
                else f"old_string occurs {count} times; add context or set replace_all."
            )
        data = text.replace(old_string, new_string).encode("utf-8")
        self.call(
            "workspace_write",
            {"path": path, "data": base64.b64encode(data).decode(), "expected_sha256": expected},
        )
        self.hashes[path] = hashlib.sha256(data).hexdigest()
        return f"Edited {path}. {SAVED_NOTE}"

    def write(self, path: str, content: str) -> str:
        before = self.call("workspace_stat", {"path": path})
        if before.get("exists"):
            answer = self.call("confirm", {"title": "Overwrite workspace file?", "message": path})
            if not answer.get("confirmed"):
                raise RuntimeError("The user declined this overwrite.")
        data = content.encode("utf-8")
        self.call(
            "workspace_write",
            {
                "path": path,
                "data": base64.b64encode(data).decode(),
                "expected_sha256": before.get("sha256"),
            },
        )
        self.hashes[path] = hashlib.sha256(data).hexdigest()
        return f"Saved {path}. {SAVED_NOTE}"


def call_tool(name: str, args: dict, actions: dict, workspace: Workspace) -> list[dict]:
    """The MCP content of one tool call (text, plus any image previews a canvas action returns)."""
    if name in ("read", "edit", "write"):
        return [{"type": "text", "text": getattr(workspace, name)(**args)}]
    if name not in actions:
        raise RuntimeError(f"Unknown tool {name}.")
    result = bridge(actions[name], args)
    previews = result.pop("image_previews", None) if isinstance(result, dict) else None
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}] + [
        {"type": "image", "data": p["data"], "mimeType": p["mimeType"]} for p in previews or []
    ]


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    tools = tool_list()
    actions = {t["name"]: t["action"] for t in json.loads((HERE / "agent_tools.json").read_text())}
    workspace, lock = Workspace(), threading.Lock()

    def reply(message: dict) -> None:
        with lock:
            stdout.write(json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False) + "\n")
            stdout.flush()

    def run(message_id, params: dict) -> None:
        try:
            content = call_tool(
                params.get("name"), params.get("arguments") or {}, actions, workspace
            )
            reply({"id": message_id, "result": {"content": content}})
        except (RuntimeError, OSError, ValueError, TypeError, KeyError) as error:
            reply(
                {
                    "id": message_id,
                    "result": {"content": [{"type": "text", "text": str(error)}], "isError": True},
                }
            )

    for line in stdin:
        message = json.loads(line)
        method, message_id = message.get("method"), message.get("id")
        if message_id is None:
            continue  # notifications (initialized, cancelled)
        if method == "initialize":
            version = (message.get("params") or {}).get("protocolVersion", "2025-06-18")
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "studio", "version": "1"},
            }
            reply({"id": message_id, "result": result})
        elif method == "tools/list":
            reply({"id": message_id, "result": {"tools": tools}})
        elif method == "tools/call":
            # Calls run side by side: one waiting for the user's approval must not hold up the others.
            threading.Thread(
                target=run, args=(message_id, message.get("params") or {}), daemon=True
            ).start()
        elif method == "ping":
            reply({"id": message_id, "result": {}})
        else:
            reply(
                {"id": message_id, "error": {"code": -32601, "message": f"Unknown method {method}"}}
            )


if __name__ == "__main__":
    serve()
