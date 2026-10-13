"""Offline RPC/broker checks: no provider, queue, credentials or main writes."""

import asyncio
import base64
import json
import queue
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from . import pi_chat


class Socket:
    closed = False

    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)

    async def close(self):
        self.closed = True


class Transport:
    def __init__(self, *args):
        self.workspace = {
            "workflow_id": args[1]["COMFY_PI_WORKFLOW_ID"],
            "graph_id": args[1]["COMFY_PI_WORKFLOW_GRAPH_ID"],
            "root": args[1]["COMFY_PI_WORKSPACE_DIR"],
            "file_tools_scoped": True,
            "shell_enabled": False,
        }
        self.records = queue.Queue()
        self.commands = []
        self.aborted = False
        self.model = {"provider": "offline", "id": "fixture"}
        self.thinking = "off"

    def send(self, record):
        self.commands.append(record)
        if record["type"] in {"set_model", "set_thinking_level"}:
            if record["type"] == "set_model":
                self.model = {"provider": record["provider"], "id": record["modelId"]}
            else:
                self.thinking = record["level"]
            self.records.put({"type": "response", "id": record["id"], "success": True})
        if record["type"] == "get_state":
            self.records.put(
                {
                    "type": "response",
                    "id": record["id"],
                    "success": True,
                    "data": {"model": self.model, "thinkingLevel": self.thinking},
                }
            )
        if record["type"] == "get_commands":
            self.records.put(
                {
                    "type": "response",
                    "id": record["id"],
                    "success": True,
                    "data": {"commands": [{"name": "comfy"}, {"name": "ponytail"}]},
                }
            )

        if record.get("message") == "/ponytail full":
            self.records.put(
                {
                    "type": "response",
                    "id": record["id"],
                    "success": True,
                    "data": {"disposition": "handled"},
                }
            )
        if record.get("message", "").startswith("/comfy"):
            self.records.put(
                {
                    "type": "extension_ui_request",
                    "id": "check",
                    "method": "notify",
                    "message": json.dumps(
                        {
                            "type": "comfy_tools",
                            "schema_only_prompt": False,
                            "workspace": self.workspace,
                            "skills": {
                                "names": sorted(pi_chat.REQUIRED_SKILLS),
                                "default_loaded": True,
                            },
                            "extensions": {
                                "sol_pi": {
                                    "version": "fixture",
                                    "actionFusion": True,
                                    "observationPack": True,
                                    "evidencePreservingReducer": False,
                                    "onlineContextCompact": False,
                                },
                                "ponytail": {"version": "fixture", "mode": "full"},
                            },
                            "active": [
                                "comfy_read_workflow",
                                "comfy_node_catalog",
                                "comfy_edit_workflow",
                                "comfy_run_workflow",
                                "comfy_execution_results",
                            ],
                        }
                    ),
                }
            )
            self.records.put(
                {
                    "type": "response",
                    "id": record["id"],
                    "success": True,
                    "data": {"disposition": "handled"},
                }
            )

    def next_record(self, deadline):
        try:
            return self.records.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError from None

    def abort(self):
        self.aborted = True


class PiChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.workspace_temp = tempfile.TemporaryDirectory()
        self.workspace_id = str(uuid.uuid4())
        self.patch = patch.object(pi_chat, "_PiRPCProcess", Transport)
        self.patch.start()
        self.socket = Socket()
        with patch.object(
            pi_chat,
            "_build_command",
            return_value=(
                [
                    "pi",
                    "--mode",
                    "rpc",
                    "--no-tools",
                    "--no-extensions",
                    "--system-prompt",
                    "You are a bounded structured-output assistant.",
                ],
                None,
                None,
                None,
            ),
        ):
            self.session = pi_chat.PiChatSession(
                self.socket,
                "http://127.0.0.1:8189",
                workflow_id=self.workspace_id,
                workspace_root=Path(self.workspace_temp.name) / "workspaces",
            )
        await self.session.start()

    async def asyncTearDown(self):
        await self.session.close()
        self.patch.stop()
        self.workspace_temp.cleanup()

    async def test_isolated_auth_keeps_operator_package_profile(self):
        config = {
            "version": 1,
            "provider": "openai",
            "model": "gpt-4.1",
            "thinking_level": "low",
            "credential_source": "studio_keychain",
        }
        with (
            patch.dict("os.environ", {"PI_CODING_AGENT_DIR": "/tmp/operator-pi"}),
            patch.object(
                pi_chat,
                "provider_credential_environment",
                return_value={"OPENAI_API_KEY": "offline-fixture"},
            ),
        ):
            session = pi_chat.PiChatSession(
                Socket(),
                "http://127.0.0.1:8189",
                config,
                workflow_id=self.workspace_id,
                workspace_root=Path(self.workspace_temp.name) / "workspaces",
            )
        try:
            self.assertEqual(session.environment["COMFY_PI_PARENT_AGENT_DIR"], "/tmp/operator-pi")
            self.assertEqual(session.environment["PI_CODING_AGENT_DIR"], session.profile.name)
            self.assertNotEqual(session.environment["PI_CODING_AGENT_DIR"], "/tmp/operator-pi")
            await session.start()
            self.assertEqual(session.socket.events[-1]["extensions"]["ponytail"]["mode"], "full")
        finally:
            await session.close()

    async def test_browser_workspace_downloads_are_read_only_and_confined(self):
        data = "name,prefecture\n佐藤 花子,東京都\n".encode()
        await self.session.workspace_action(
            "workspace_write",
            {
                "path": "sample.csv",
                "data": base64.b64encode(data).decode(),
                "expected_sha256": None,
            },
        )
        self.assertEqual(self.socket.events[-1], {"type": "workspace_saved", "path": "sample.csv"})
        commands = list(self.session.transport.commands)
        await self.session.client_message({"type": "workspace_files", "id": "list"})
        self.assertEqual(
            self.socket.events[-1]["result"]["files"],
            [{"path": "sample.csv", "size": len(data)}],
        )
        await self.session.client_message(
            {"type": "workspace_file", "path": "sample.csv", "id": "download"}
        )
        self.assertEqual(self.socket.events[-1]["id"], "download")
        self.assertEqual(self.socket.events[-1]["result"]["path"], "sample.csv")
        self.assertEqual(base64.b64decode(self.socket.events[-1]["result"]["data"]), data)
        for path in ("../escape.csv", "/etc/passwd", "missing.csv"):
            await self.session.client_message({"type": "workspace_file", "path": path})
            self.assertIn("error", self.socket.events[-1])
            self.assertNotIn("result", self.socket.events[-1])
        self.assertEqual(self.session.transport.commands, commands)
        self.assertTrue(self.socket.events[0].get("workspace_files_ui"))

    async def test_persistent_transport_and_user_prompt_boundary(self):
        self.assertNotIn("--system-prompt", self.session.command)
        from . import pi_cli

        self.assertNotIn(pi_cli.PI_SYSTEM_PROMPT, self.session.command)
        structured_command, _, _, _ = pi_cli._build_command(
            None, offline=False, structured_output=True
        )
        self.assertIn(pi_cli.PI_SYSTEM_PROMPT, structured_command)
        self.assertIn("--system-prompt", structured_command)
        self.assertEqual(self.socket.events[-1]["type"], "ready")
        self.assertEqual(
            [c["type"] for c in self.session.transport.commands],
            ["get_state", "get_commands", "prompt", "prompt"],
        )
        self.assertNotIn("--no-tools", self.session.command)
        self.assertIn("--no-extensions", self.session.command)
        self.assertNotIn("--tools", self.session.command)
        self.assertIn("--exclude-tools", self.session.command)
        self.assertIn("bash,powershell,grep,find,ls", self.session.command)
        self.assertEqual(self.session.environment["COMFY_PI_WORKFLOW_ID"], self.workspace_id)
        for builtin in ("builtin:mcp", "builtin:codemode", "builtin:tool-search"):
            self.assertIn(builtin, self.session.command)
        self.assertEqual(self.session.transport.commands[-1]["message"], "/comfy --check")
        await self.session.client_message({"type": "prompt", "message": "Inspect my canvas"})
        with self.assertRaisesRegex(ValueError, "current reply"):
            await self.session.client_message({"type": "prompt", "message": "Again"})
        self.assertEqual(self.session.transport.commands[-1]["type"], "prompt")
        # SoL-Pi/core hooks can continue after agent_end. Do not release the
        # input, pending approvals or turn budget until agent_settled.
        self.session.transport.records.put({"type": "agent_end", "messages": []})
        await asyncio.sleep(0.03)
        self.assertTrue(self.session.busy)
        self.assertFalse(hasattr(self.session, "deadline"))
        with patch.object(pi_chat.time, "monotonic", return_value=time.monotonic() + 181):
            await asyncio.sleep(0)
            self.assertTrue(self.session.busy)
            self.assertFalse(any(c["type"] == "abort" for c in self.session.transport.commands))
        self.session.transport.records.put({"type": "agent_settled"})
        for _ in range(30):
            if not self.session.busy:
                break
            await asyncio.sleep(0.01)
        await self.session.client_message({"type": "prompt", "message": "Continue"})
        self.assertEqual(
            sum(
                c["type"] == "prompt" and not c.get("id", "").startswith("chat_")
                for c in self.session.transport.commands
            ),
            2,
        )

    async def test_local_extension_command_does_not_stall_chat(self):
        await self.session.client_message({"type": "prompt", "message": "/comfy"})
        for _ in range(30):
            if not self.session.busy:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(self.session.busy)
        self.assertTrue(any(event["type"] == "command_handled" for event in self.socket.events))

    async def test_model_configuration_preserves_session_and_checks_profile(self):
        await self.session.start()
        config = {
            "version": 1,
            "provider": "offline-other",
            "model": "alternative",
            "thinking_level": "high",
            "credential_source": "pi_managed",
        }
        transport = self.session.transport
        await self.session.client_message({"type": "configure", "pi_config": config})
        self.assertIs(self.session.transport, transport)
        self.assertEqual(self.session.configuration(), config)
        self.assertEqual(self.socket.events[-1], {"type": "model_config", "pi_config": config})
        self.session.busy = True
        with self.assertRaisesRegex(ValueError, "finish"):
            await self.session.client_message({"type": "configure", "pi_config": config})
        self.session.busy = False
        with self.assertRaisesRegex(ValueError, "New chat"):
            await self.session.client_message(
                {
                    "type": "configure",
                    "pi_config": {**config, "credential_source": "studio_keychain"},
                }
            )
        self.assertFalse(hasattr(self.session, "deadline"))
        self.assertFalse(hasattr(self.session, "expire_turn"))

    async def test_canvas_response_is_bound_to_pending_action(self):
        action = asyncio.create_task(self.session.canvas_action("read", {}))
        await asyncio.sleep(0)
        id = self.socket.events[-1]["id"]
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            await self.session.client_message({"type": "comfy_result", "id": "other", "result": {}})
        await self.session.client_message(
            {"type": "comfy_result", "id": id, "result": {"nodes": []}}
        )
        self.assertEqual(await action, {"nodes": []})
        self.assertFalse(self.session.pending)

    async def test_abort_cancels_pending_actions(self):
        action = asyncio.create_task(self.session.canvas_action("catalog", {}))
        await asyncio.sleep(0)
        await self.session.client_message({"type": "abort"})
        with self.assertRaises(asyncio.CancelledError):
            await action
        self.assertEqual(self.session.transport.commands[-1]["type"], "abort")

    async def test_permission_ids_and_types_cannot_be_forged(self):
        with self.assertRaises(ValueError):
            await self.session.client_message(
                {"type": "extension_ui_response", "id": "unknown", "confirmed": True}
            )
        self.session.dialogs["permission"] = "confirm"
        with self.assertRaises(ValueError):
            await self.session.client_message(
                {
                    "type": "extension_ui_response",
                    "id": "permission",
                    "confirmed": "true",
                }
            )
        self.session.dialogs["permission"] = "confirm"
        await self.session.client_message(
            {"type": "extension_ui_response", "id": "permission", "confirmed": False}
        )
        self.assertIs(self.session.transport.commands[-1]["confirmed"], False)

    async def test_model_cannot_send_arbitrary_rpc_commands(self):
        for kind in ("bash", "set_model", "write", "start"):
            with self.assertRaises(ValueError):
                await self.session.client_message({"type": kind})
        with self.assertRaises(ValueError):
            await self.session.canvas_action("install", {})

    async def test_close_stops_child_and_is_idempotent(self):
        await self.session.close()
        await self.session.close()
        self.assertTrue(self.session.transport.aborted)

    def test_reasoning_is_removed_from_all_event_shapes(self):
        self.assertIsNone(
            pi_chat.public_event(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "delta": "private",
                    },
                }
            )
        )
        event = {
            "type": "agent_end",
            "messages": [
                {
                    "content": [
                        {"type": "thinking", "thinking": "private"},
                        {"type": "text", "text": "answer"},
                    ]
                }
            ],
        }
        self.assertNotIn("private", json.dumps(pi_chat.public_event(event)))
        self.assertIn("answer", json.dumps(pi_chat.public_event(event)))
        self.assertIn("private", json.dumps(event))

    def test_loopback_same_origin_and_dns_rebinding_checks(self):
        def request(host="127.0.0.1:8189", origin="http://127.0.0.1:8189", remote="127.0.0.1"):
            return SimpleNamespace(
                host=host, scheme="http", remote=remote, headers={"Origin": origin}
            )

        pi_chat.local_request(request(), browser=True)
        for req in (
            request(origin="https://evil.example"),
            request(host="evil.example", origin="http://evil.example"),
            request(remote="10.0.0.2"),
            request(remote=None),
        ):
            with self.assertRaises(web.HTTPForbidden):
                pi_chat.local_request(req, browser=True)


class PiChatRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_socket_and_authenticated_bridge(self):
        import server

        app = web.Application()
        fake = SimpleNamespace(app=app, routes=web.RouteTableDef())
        with (
            tempfile.TemporaryDirectory() as workspace_temp,
            patch("folder_paths.get_user_directory", return_value=workspace_temp),
            patch.object(server.PromptServer, "instance", fake, create=True),
            patch.object(pi_chat, "_PiRPCProcess", side_effect=Transport) as spawn,
        ):
            pi_chat.install_pi_chat_routes()
            app.add_routes(fake.routes)
            async with TestClient(TestServer(app)) as client:
                origin = str(client.make_url("/")).rstrip("/")
                with self.assertRaises(WSServerHandshakeError):
                    await client.ws_connect(
                        pi_chat.PREFIX + "/ws",
                        headers={"Origin": "https://evil.example"},
                    )
                socket = await client.ws_connect(pi_chat.PREFIX + "/ws", headers={"Origin": origin})
                await socket.send_json({"type": "start"})
                missing = await socket.receive_json(timeout=5)
                self.assertIn("Refresh ComfyUI", missing["error"])
                await socket.send_json({"type": "start", "workflow_id": "legacy-workflow"})
                ready = await socket.receive_json(timeout=5)
                self.assertEqual(ready["type"], "ready")
                self.assertEqual(ready["workspace"]["graph_id"], "legacy-workflow")
                self.assertEqual(
                    ready["workspace"]["workflow_id"],
                    pi_chat.workspace_id_for_graph("legacy-workflow"),
                )
                response = await client.post(
                    pi_chat.PREFIX + "/bridge/fake", json={"action": "run", "args": {}}
                )
                self.assertEqual(response.status, 403)
                environment = spawn.call_args.args[1]
                url = environment["COMFY_PI_BRIDGE_URL"].removeprefix(origin)
                headers = {"Authorization": "Bearer " + environment["COMFY_PI_BRIDGE_TOKEN"]}
                payload = {
                    "path": "script.py",
                    "data": base64.b64encode(b"safe data").decode(),
                    "expected_sha256": None,
                }
                response = await client.post(
                    url,
                    headers=headers,
                    json={"action": "workspace_write", "args": payload},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    await socket.receive_json(),
                    {"type": "workspace_saved", "path": "script.py"},
                )
                await socket.send_json({"type": "workspace_files"})
                self.assertEqual(
                    (await socket.receive_json())["result"]["files"],
                    [{"path": "script.py", "size": 9}],
                )
                response = await client.post(
                    url,
                    headers=headers,
                    json={"action": "workspace_read", "args": {"path": "script.py"}},
                )
                self.assertEqual(
                    base64.b64decode((await response.json())["result"]["data"]),
                    b"safe data",
                )
                response = await client.post(
                    url,
                    headers=headers,
                    json={
                        "action": "workspace_write",
                        "args": {**payload, "path": "../escape.py"},
                    },
                )
                self.assertEqual(response.status, 400)
                response = await client.post(
                    url,
                    headers=headers,
                    json={
                        "action": "workspace_write",
                        "args": {**payload, "then_run": {"command": "false"}},
                    },
                )
                self.assertEqual(response.status, 400)
                self.assertFalse((Path(workspace_temp) / "escape.py").exists())
                await socket.send_json({"type": "prompt", "message": ""})
                self.assertEqual((await socket.receive_json())["type"], "chat_error")
                await socket.close()


if __name__ == "__main__":
    unittest.main()
