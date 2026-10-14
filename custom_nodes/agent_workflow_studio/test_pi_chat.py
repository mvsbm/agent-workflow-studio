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
from unittest.mock import AsyncMock, patch

from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from . import pi_chat
from .test_workflow_contract import CONTRACT


class Socket:
    closed = False

    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)

    async def close(self):
        self.closed = True


class Transport:
    addons = True

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
                    "data": {
                        "commands": [{"name": "comfy"}]
                        + ([{"name": "ponytail"}] if self.addons else [])
                    },
                }
            )

        if record.get("message") == "/ponytail ultra":
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
                                "ponytail": {"version": "fixture", "mode": "ultra"},
                            }
                            if self.addons
                            else {},
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

    async def test_adding_nodes_requires_an_approved_plan(self):
        add = {"operations": [{"op": "add", "key": "a", "class_type": "X"}]}
        with patch.object(
            self.session, "canvas_action", AsyncMock(return_value={"ok": True})
        ) as canvas:
            with self.assertRaisesRegex(ValueError, "approved contract"):
                await self.session.agent_action("edit", add)
            # Small fixes need no plan.
            await self.session.agent_action(
                "edit", {"operations": [{"op": "title", "node": 1, "title": "x"}]}
            )
            self.assertEqual(canvas.await_count, 1)
            # The agent cannot call the approval card or gate checks directly.
            for action in ("contract_approve", "contract_evidence", "gates"):
                with self.assertRaisesRegex(ValueError, "comfy_propose_contract"):
                    await self.session.agent_action(action, {})

    async def test_structural_edits_are_checked_automatically(self):
        self.session.contract = CONTRACT
        analysis = {
            "summary": "2 node(s), 1 output node(s), 1 that will not run, 1 problem(s).",
            "problems": [
                {
                    "kind": "unconnected_input",
                    "message": 'Required input "vae" (VAE) is not connected.',
                }
            ],
            "will_not_run": [{"id": 4, "title": "Loose trim", "class_type": "StringTrim"}],
        }
        calls = []

        evidence = {
            "nodes": [{"title": "Loose trim", "step": None}],
            "analysis": {"outputs": [], "problems": []},
        }

        async def canvas(action, args):
            calls.append(action)
            return {"analyze": analysis, "contract_evidence": evidence}.get(
                action, {"node_count": 2}
            )

        with patch.object(self.session, "canvas_action", side_effect=canvas):
            result = await self.session.agent_action(
                "edit",
                {"operations": [{"op": "connect", "from": 1, "output": 0, "to": 2, "input": "x"}]},
            )
            await self.session.agent_action(
                "edit", {"operations": [{"op": "move", "node": 1, "position": [0, 0]}]}
            )
        self.assertEqual(calls, ["edit", "analyze", "contract_evidence", "edit"])
        self.assertFalse(result["graph_check"]["contract"]["aligned"])
        self.assertIn("Contract: ⚠", self.socket.events[-1]["text"])
        self.assertEqual(
            result["graph_check"]["problems"], ['Required input "vae" (VAE) is not connected.']
        )
        self.assertEqual(result["graph_check"]["will_not_run"], ["Loose trim"])
        notice = self.socket.events[-1]
        self.assertEqual(notice["type"], "graph_check")
        self.assertIn("Will not run (reaches no output): Loose trim", notice["text"])

    async def test_workspace_writes_need_a_contract_except_scratch(self):
        write = {"path": "solution.py", "data": "cHJpbnQoMSkK", "expected_sha256": None}
        with self.assertRaisesRegex(ValueError, "needs an approved contract"):
            await self.session.agent_action("workspace_write", write)
        scratch = await self.session.agent_action(
            "workspace_write", {**write, "path": "tmp/notes.md"}
        )
        self.assertTrue(scratch.get("sha256"))
        info = await self.session.agent_action("workspace_info", {})  # reading stays open
        self.assertIn("tmp/notes.md", json.dumps(info))
        self.session.contract = CONTRACT
        self.assertTrue((await self.session.agent_action("workspace_write", write)).get("sha256"))

    async def test_tool_approval_shows_code_and_stores_the_approved_copy(self):
        self.session.contract = CONTRACT
        code = "def run(inputs):\n    return {'text': inputs['text'].upper()}\n"
        await self.session.agent_action(
            "workspace_write",
            {
                "path": "tools/upper.py",
                "data": base64.b64encode(code.encode()).decode(),
                "expected_sha256": None,
            },
        )
        proposal = {
            "name": "upper",
            "description": "Uppercase text",
            "inputs": [{"name": "text", "kind": "text"}],
            "outputs": [{"name": "text", "kind": "text"}],
        }
        with patch.object(
            self.session, "canvas_action", AsyncMock(return_value={"approved": True})
        ) as canvas:
            result = await self.session.agent_action("tool_propose", proposal)
        action, args = canvas.await_args.args
        self.assertEqual((action, args["title"]), ("tool_approve", "Approve tool upper?"))
        self.assertTrue(args["text"].rstrip().endswith("return {'text': inputs['text'].upper()}"))
        self.assertTrue(result["approved"])
        self.assertEqual(self.session.repo.load_tool("upper")[0], code)
        self.assertEqual(self.session.repo.log()[0]["message"], "Tool approved: upper")
        with self.assertRaisesRegex(ValueError, "comfy_propose_contract"):
            await self.session.agent_action("tool_approve", {})
        giant = code + "".join(f"x{i} = {i}\n" for i in range(500))
        await self.session.agent_action(
            "workspace_write",
            {
                "path": "tools/upper.py",
                "data": base64.b64encode(giant.encode()).decode(),
                "expected_sha256": self.session.workspace.perform(
                    "workspace_read", {"path": "tools/upper.py"}
                )["sha256"],
            },
        )
        with self.assertRaisesRegex(ValueError, "one tool per stage"):
            await self.session.agent_action("tool_propose", proposal)

    async def test_contract_status_comes_from_browser_evidence(self):
        self.session.contract = CONTRACT
        evidence = {
            "nodes": [
                {"id": 1, "title": "Label", "step": "classify", "fuzzy": False},
                {"id": 2, "title": "Loose", "step": None},
            ],
            "gate_results": {"classify": "passed", "tidy": "needs_user_review"},
            "run": {"prompt_id": "p", "current": True},
        }
        calls = []

        async def canvas(action, args):
            calls.append((action, args))
            return evidence if action == "contract_evidence" else {"ok": True}

        with patch.object(self.session, "canvas_action", side_effect=canvas):
            status = await self.session.agent_action("contract_status", {})
            await self.session.agent_action(
                "edit", {"operations": [{"op": "title", "node": 1, "title": "Label"}]}
            )
        self.assertEqual(
            {s["step"]: s["state"] for s in status["steps"]},
            {"classify": "verified", "tidy": "planned"},
        )
        self.assertEqual(status["progress"], "1/2 steps verified")
        self.assertEqual(status["nodes_outside_steps"], ["Loose"])
        # Gates to evaluate come from the approved contract; edits carry the step group titles.
        self.assertEqual([g["step"] for g in calls[0][1]["gates"]], ["classify", "tidy"])
        self.assertEqual(
            calls[1][1]["contract_steps"][0]["title"],
            "1 · Decide whether the review is positive or negative.",
        )

    async def test_plan_approval_comes_from_the_browser_and_is_versioned(self):
        answers = {
            "contract_approve": {"approved": True},
            "edit": {"ok": True},
            "analyze": {"summary": "ok", "problems": [], "will_not_run": []},
            "contract_evidence": {"nodes": [], "analysis": {"outputs": [], "problems": []}},
        }
        with patch.object(
            self.session,
            "canvas_action",
            AsyncMock(side_effect=lambda action, _args: answers[action]),
        ) as canvas:
            result = await self.session.agent_action("contract_propose", {"contract": CONTRACT})
            self.assertTrue(result["approved"])
            approval = canvas.await_args_list[0].args
            self.assertEqual(approval[0], "contract_approve")
            self.assertIn("PURPOSE: Label customer reviews", approval[1]["text"])
            await self.session.agent_action(
                "edit", {"operations": [{"op": "add", "key": "a", "class_type": "X"}]}
            )
        log = self.session.repo.log()
        self.assertEqual(
            (log[0]["author"], log[0]["message"]),
            ("user", f"Contract approved: {CONTRACT['purpose']}"),
        )
        self.assertEqual(
            await self.session.agent_action("contract_get", {}), {"contract": CONTRACT}
        )
        with patch.object(
            self.session, "canvas_action", AsyncMock(return_value={"gates": []})
        ) as canvas:
            await self.session.agent_action("contract_gates", {})
        action, args = canvas.await_args.args
        self.assertEqual(action, "gates")
        self.assertEqual([g["step"] for g in args["gates"]], ["classify", "tidy"])
        self.assertEqual(args["gates"][0]["check"]["kind"], "regex")
        # A later chat on the same workflow inherits the approved plan.
        later = pi_chat.PiChatSession(
            Socket(),
            "http://127.0.0.1:8189",
            workflow_id=self.workspace_id,
            workspace_root=Path(self.workspace_temp.name) / "workspaces",
        )
        self.assertEqual(later.contract, CONTRACT)

    async def test_denied_plans_and_open_questions_are_not_approved(self):
        with patch.object(
            self.session, "canvas_action", AsyncMock(return_value={"approved": False})
        ):
            result = await self.session.agent_action("contract_propose", {"contract": CONTRACT})
        self.assertFalse(result["approved"])
        self.assertIsNone(self.session.contract)
        with (
            patch.object(self.session, "canvas_action", AsyncMock()) as canvas,
            self.assertRaisesRegex(ValueError, "open questions"),
        ):
            await self.session.agent_action(
                "contract_propose",
                {"contract": {**CONTRACT, "open_questions": ["Which language?"]}},
            )
        canvas.assert_not_awaited()

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
            self.assertEqual(session.socket.events[-1]["extensions"]["ponytail"]["mode"], "ultra")
        finally:
            await session.close()

    async def test_chat_starts_without_optional_addons(self):
        class BareTransport(Transport):
            addons = False

        with patch.object(pi_chat, "_PiRPCProcess", BareTransport):
            session = pi_chat.PiChatSession(
                Socket(),
                "http://127.0.0.1:8189",
                None,
                workflow_id=self.workspace_id,
                workspace_root=Path(self.workspace_temp.name) / "workspaces",
            )
            try:
                await session.start()
                ready = session.socket.events[-1]
                self.assertEqual(ready["type"], "ready")
                self.assertEqual(ready["extensions"], {})
                sent = [command.get("message") for command in session.transport.commands]
                self.assertNotIn("/ponytail ultra", sent)
                self.assertIn("/comfy --check", sent)
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


class InputFilesTests(unittest.TestCase):
    def test_lists_and_previews_text_read_only_inside_the_input_folder(self):
        from .pi_chat import input_files

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "input"
            (base / "nayose").mkdir(parents=True)
            (base / "nayose" / "a.csv").write_bytes("名前,カナ\r\n山田,ﾔﾏﾀﾞ\r\n".encode("cp932"))
            (base / "nayose" / "b.bin").write_bytes(b"\0\1")
            (Path(temp) / "secret.txt").write_text("no")
            listing = input_files(base, "nayose")
            self.assertEqual(
                [f["path"] for f in listing["files"]], ["nayose/a.csv", "nayose/b.bin"]
            )
            preview = input_files(base, "nayose/a.csv")
            self.assertEqual((preview["encoding"], preview["lines"]), ("cp932", 2))
            self.assertEqual(preview["head"], ["名前,カナ", "山田,ﾔﾏﾀﾞ"])
            for bad, message in (
                ("../secret.txt", "inside"),
                ("nayose/b.bin", "Binary"),
                ("x.csv", "No such"),
            ):
                with self.assertRaisesRegex(ValueError, message):
                    input_files(base, bad)


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
                    "path": "tmp/script.py",  # scratch: writable before any contract
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
                    {"type": "workspace_saved", "path": "tmp/script.py"},
                )
                await socket.send_json({"type": "workspace_files"})
                self.assertEqual(
                    (await socket.receive_json())["result"]["files"],
                    [{"path": "tmp/script.py", "size": 9}],
                )
                response = await client.post(
                    url,
                    headers=headers,
                    json={"action": "workspace_read", "args": {"path": "tmp/script.py"}},
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
                        "args": {**payload, "path": "tmp/../escape.py"},
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
