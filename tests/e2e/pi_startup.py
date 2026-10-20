"""Smoke test: the real Pi CLI loads the chat extension with and without the user's add-ons.

Starts Pi in RPC mode exactly as the chat does, asks for its commands, and exits.
No provider request is made. Skips when Pi is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile

from fixture import COMFY, PACKAGE

sys.path.insert(0, str(COMFY))
from custom_nodes.agent_workflow_studio import pi_cli  # noqa: E402


def chat_command() -> list[str]:
    command, *_ = pi_cli._build_command(None, offline=False)
    index = command.index("--system-prompt")
    del command[index : index + 2]
    dropped = {"--no-tools", "--no-skills", "--no-context-files", "--no-prompt-templates"}
    return [arg for arg in command if arg not in dropped] + [
        "--exclude-tools",
        "bash,powershell,grep,find,ls",
        "--extension",
        str(PACKAGE / "pi_chat_extension.ts"),
    ]


def commands(profile: str | None) -> tuple[set[str], list[str]]:
    """Pi's commands and its active tools (from /comfy --check)."""
    with tempfile.TemporaryDirectory() as workspace:
        env = {
            **pi_cli._pi_environment(),
            "COMFY_PI_BRIDGE_URL": "http://127.0.0.1:9/unused",
            "COMFY_PI_BRIDGE_TOKEN": "unused",
            "COMFY_PI_WORKSPACE_DIR": workspace,
            "COMFY_PI_WORKFLOW_ID": "00000000-0000-4000-8000-000000000000",
            "COMFY_PI_WORKFLOW_GRAPH_ID": "smoke",
        }
        if profile is not None:
            env["PI_CODING_AGENT_DIR"] = env["COMFY_PI_PARENT_AGENT_DIR"] = profile
        result = subprocess.run(
            chat_command(),
            input=json.dumps({"type": "get_commands", "id": "smoke"})
            + "\n"
            + json.dumps({"type": "prompt", "id": "check", "message": "/comfy --check"})
            + "\n",
            capture_output=True,
            text=True,
            env=env,
            cwd=workspace,
            timeout=60,
        )
    if result.returncode:
        raise AssertionError(f"Pi exited {result.returncode}: {result.stderr[-1500:]}")
    names, active = None, None
    for line in result.stdout.splitlines():
        record = json.loads(line)
        if record.get("id") == "smoke":
            assert record.get("success"), record
            names = {item["name"] for item in record["data"]["commands"]}
        if record.get("method") == "notify" and '"comfy_tools"' in record.get("message", ""):
            active = json.loads(record["message"])["active"]
    if names is None or active is None:
        raise AssertionError("Pi returned no commands or tool check:\n" + result.stdout[-1500:])
    return names, active


def main() -> int:
    if not shutil.which("pi"):
        print("SKIP pi_startup: Pi CLI not installed")
        return 0
    with tempfile.TemporaryDirectory() as empty_profile:
        names, active = commands(empty_profile)
    assert "comfy" in names and "ponytail" not in names, names
    # Every tool in the shared definition file is registered (the same file feeds Claude Code).
    tools = {t["name"] for t in json.loads((PACKAGE / "agent_tools.json").read_text())}
    assert tools <= set(active), sorted(tools - set(active))
    print("PASS pi_startup (empty Pi profile, no add-ons)")
    names, _ = commands(None)
    assert "comfy" in names, names
    print(
        f"PASS pi_startup (user Pi profile, add-ons: {'ponytail' in names and 'Ponytail' or 'none'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
