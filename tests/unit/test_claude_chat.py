"""Claude Code as the chat agent, offline: locked-down launch, the shared tools, the workspace file
rules, the event translation and the in-panel dialogs. No Claude process or provider is used."""

# Verifies: R12, R21
import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from custom_nodes.agent_workflow_studio import claude_chat, claude_mcp

from .test_pi_chat import Socket


class FakeWorkspace:
    """The bridge's workspace actions on a dict, recording overwrite confirmations."""

    def __init__(self, confirm=True):
        self.files, self.confirm, self.asked = {}, confirm, []

    def __call__(self, action, args):
        data = self.files.get(args.get("path"))
        digest = hashlib.sha256(data).hexdigest() if data is not None else None
        if action == "workspace_read":
            return {"data": base64.b64encode(data).decode(), "sha256": digest}
        if action == "workspace_stat":
            return {"exists": data is not None, "sha256": digest}
        if action == "confirm":
            self.asked.append(args["message"])
            return {"confirmed": self.confirm}
        assert action == "workspace_write" and args["expected_sha256"] == digest, "stale write"
        self.files[args["path"]] = base64.b64decode(args["data"])
        return {}


class ClaudeToolsTests(unittest.TestCase):
    def test_claude_gets_every_shared_tool_and_the_file_tools(self) -> None:
        shared = json.loads((claude_mcp.HERE / "agent_tools.json").read_text())
        tools = claude_mcp.tool_list()
        self.assertEqual(
            [t["name"] for t in tools], [t["name"] for t in shared] + ["read", "edit", "write"]
        )
        self.assertNotIn("$ref", json.dumps(tools))  # the agent schemas are inlined
        schema = {t["name"]: t["inputSchema"] for t in tools}
        self.assertIn(
            "requests", schema["comfy_propose_contract"]["properties"]["contract"]["properties"]
        )
        self.assertIn("steps", schema["comfy_propose_design"]["properties"]["design"]["properties"])
        self.assertIn("wires", schema["comfy_build_step"]["properties"])

    def test_edit_needs_a_fresh_read_and_write_asks_before_replacing(self) -> None:
        fake = FakeWorkspace()
        files = claude_mcp.Workspace(fake)
        files.write("notes.md", "a\nb\n")
        self.assertEqual(fake.asked, [])  # a new file needs no question
        with self.assertRaisesRegex(RuntimeError, "Read the workspace file"):
            claude_mcp.Workspace(fake).edit("notes.md", "a", "x")
        self.assertIn("     1\ta", files.read("notes.md"))
        files.edit("notes.md", "b", "c")
        self.assertEqual(fake.files["notes.md"], b"a\nc\n")
        fake.files["notes.md"] = b"changed elsewhere"
        with self.assertRaisesRegex(RuntimeError, "changed since you read"):
            files.edit("notes.md", "a", "x")
        files.write("notes.md", "new")
        self.assertEqual(fake.asked, ["notes.md"])
        refused = FakeWorkspace(confirm=False)
        refused.files["x"] = b"1"
        with self.assertRaisesRegex(RuntimeError, "declined"):
            claude_mcp.Workspace(refused).write("x", "y")
        self.assertEqual(refused.files["x"], b"1")

    def test_config_is_checked(self) -> None:
        self.assertEqual(
            claude_chat.validate_claude_config({"agent": "claude_code"})["model"], "default"
        )
        for bad in (
            {"agent": "claude_code", "model": "gpt-5"},
            {"agent": "claude_code", "effort": "x"},
        ):
            with self.assertRaises(ValueError):
                claude_chat.validate_claude_config(bad)


class ClaudeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket = Socket()
        with patch.object(claude_chat, "claude_cli", return_value="/usr/bin/claude"):
            self.session = claude_chat.ClaudeChatSession(
                self.socket,
                "http://127.0.0.1:8189",
                {"agent": "claude_code", "model": "haiku"},
                workflow_id=str(uuid.uuid4()),
                workspace_root=Path(self.temp.name) / "workspaces",
            )

    async def asyncTearDown(self):
        await self.session.close()
        self.temp.cleanup()

    def test_launch_is_locked_down(self) -> None:
        command = self.session.command
        value = lambda flag: command[command.index(flag) + 1]  # noqa: E731
        self.assertEqual(value("--tools"), "")  # no shell, no host files, no web
        self.assertEqual(value("--setting-sources"), "")  # not the user's settings or CLAUDE.md
        self.assertEqual(value("--allowedTools"), "mcp__studio")
        self.assertEqual(value("--permission-mode"), "dontAsk")
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(value("--model"), "haiku")
        self.assertNotIn(self.session.token, " ".join(command))  # the bridge token is env only
        self.assertEqual(self.session.environment["COMFY_PI_BRIDGE_TOKEN"], self.session.token)
        prompt = Path(value("--system-prompt-file")).read_text()
        self.assertIn(str(self.session.workspace.path), prompt)
        self.assertIn("comfyui-workflow-contracts", prompt)

    async def test_only_the_users_ponytail_plugin_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            with patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": home}):
                self.assertIsNone(claude_chat.ponytail_plugin())  # not installed: nothing loads
                folder = Path(home) / "plugins" / "cache" / "ponytail" / "9.9.0"
                (folder / ".claude-plugin").mkdir(parents=True)
                (folder / ".claude-plugin" / "plugin.json").write_text('{"version": "9.9.0"}')
                (Path(home) / "plugins" / "installed_plugins.json").write_text(
                    json.dumps({"plugins": {"ponytail@ponytail": [{"installPath": str(folder)}]}})
                )
                self.assertEqual(claude_chat.ponytail_plugin(), (str(folder), "9.9.0"))
                with patch.object(claude_chat, "claude_cli", return_value="/usr/bin/claude"):
                    session = claude_chat.ClaudeChatSession(
                        self.socket,
                        "http://127.0.0.1:8189",
                        {"agent": "claude_code", "model": "haiku"},
                        workflow_id=str(uuid.uuid4()),
                        workspace_root=Path(self.temp.name) / "workspaces",
                    )
        command = session.command
        self.assertEqual(command[command.index("--plugin-dir") + 1], str(folder))
        self.assertEqual(
            command[command.index("--setting-sources") + 1], ""
        )  # still none of theirs
        self.assertEqual(session.environment["PONYTAIL_DEFAULT_MODE"], "ultra")  # as in the Pi chat
        await session.close()

    def test_stream_events_become_the_panels_events(self) -> None:
        events = [
            {"type": "stream_event", "event": {"type": "message_start"}},
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "secret"},
                },
            },
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hi"},
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "mcp__studio__comfy_read_workflow",
                            "input": {},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": "{}"}],
                        }
                    ]
                },
            },
            {"type": "stream_event", "event": {"type": "message_stop"}},
            {"type": "result", "subtype": "success", "is_error": False},
        ]
        out = [e for event in events for e in self.session.translate(event)]
        self.assertEqual(
            [e["type"] for e in out],
            [
                "message_start",
                "message_update",
                "tool_execution_start",
                "tool_execution_end",
                "message_end",
                "agent_settled",
            ],
        )
        self.assertNotIn("secret", json.dumps(out))  # thinking never reaches the panel
        self.assertEqual(out[2]["toolName"], "comfy_read_workflow")
        self.assertEqual(out[3]["toolName"], "comfy_read_workflow")
        self.assertEqual(out[4]["message"]["content"][0]["text"], "Hi")

    def test_a_local_commands_output_is_shown_as_the_reply(self) -> None:
        # /context, /usage: Claude Code answers in the result alone, with no model turn.
        self.session.turns = 0
        out = self.session.translate(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "## Context Usage",
            }
        )
        self.assertEqual(
            [e["type"] for e in out], ["message_start", "message_end", "agent_settled"]
        )
        self.assertEqual(out[1]["message"]["content"][0]["text"], "## Context Usage")
        self.session.turns = 1  # a model reply was streamed already: not shown twice
        out = self.session.translate(
            {"type": "result", "subtype": "success", "is_error": False, "result": "Hi"}
        )
        self.assertEqual([e["type"] for e in out], ["agent_settled"])

    async def test_ask_user_and_confirm_are_answered_in_the_panel(self) -> None:
        task = asyncio.create_task(
            self.session.agent_action(
                "ask_user", {"questions": [{"question": "Which?", "options": ["A", "B"]}]}
            )
        )
        await asyncio.sleep(0)
        request = self.socket.events[-1]
        self.assertEqual((request["method"], request["options"][:2]), ("select", ["A", "B"]))
        await self.session.client_message(
            {"type": "extension_ui_response", "id": request["id"], "value": "B"}
        )
        self.assertEqual(await task, {"answers": [{"question": "Which?", "answer": "B"}]})
        task = asyncio.create_task(
            self.session.agent_action("confirm", {"title": "Overwrite?", "message": "a.md"})
        )
        await asyncio.sleep(0)
        request = self.socket.events[-1]
        await self.session.client_message(
            {"type": "extension_ui_response", "id": request["id"], "confirmed": False}
        )
        self.assertEqual(await task, {"confirmed": False})


if __name__ == "__main__":
    unittest.main()
