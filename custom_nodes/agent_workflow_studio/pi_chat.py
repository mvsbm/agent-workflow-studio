"""Native Pi RPC sessions; browser owns the canvas, ComfyUI owns execution."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from .pi_auth import (
    all_provider_api_key_env_names,
    provider_credential_environment,
)
from .pi_chat_workspace import FILE_ACTIONS, PiChatWorkspace, workspace_id_for_graph
from .pi_cli import PiCLIError, _build_command, _pi_environment, _PiRPCProcess
from .pi_model_config import validate_pi_model_config
from .project_repo import ProjectRepo
from .workflow_spec import render_spec, validate_spec

PREFIX = "/agent-workflow-studio/pi-chat"
ACTIONS = {"read", "catalog", "edit", "run", "results", "spec_approve", "gates"}
SPEC_ACTIONS = {"spec_propose", "spec_get", "spec_gates"}
REQUIRED_SKILLS = {
    "comfyui-node-creation",
    "comfyui-workflow-debugging",
    "comfyui-connector-management",
    "comfyui-node-organization",
}
MAX_MESSAGE = 1_048_576


def local_request(request, *, browser=False):
    """No remote/DNS-rebound hosts; browser sockets must be same-origin."""
    host = urlsplit("//" + request.host).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="Pi chat requires a loopback host.")
    try:
        local = ipaddress.ip_address(request.remote).is_loopback
    except (ValueError, TypeError):
        local = False
    if not local:
        raise web.HTTPForbidden(text="Pi chat is local-only.")
    if browser:
        origin = urlsplit(request.headers.get("Origin", ""))
        if origin.scheme != request.scheme or origin.netloc != request.host:
            raise web.HTTPForbidden(text="Same-origin browser connection required.")


def public_event(event):
    """Do not expose private reasoning blocks through the GUI transport."""
    if event.get("type") == "message_update":
        update = event.get("assistantMessageEvent", {})
        if update.get("type", "").startswith("thinking_"):
            return None

    def scrub(value):
        if isinstance(value, list):
            return [
                scrub(item)
                for item in value
                if not (isinstance(item, dict) and item.get("type") == "thinking")
            ]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    return scrub(event)


class PiChatSession:
    def __init__(self, socket, origin, config=None, *, workflow_id, workspace_root=None):
        if workspace_root is None:
            import folder_paths

            workspace_root = Path(folder_paths.get_user_directory()) / "pi-chat-workspaces"
        self.graph_id = workflow_id
        self.workspace = PiChatWorkspace(workspace_root, workspace_id_for_graph(workflow_id))
        # The approved plan lives in the workflow's project history, beside the workspaces.
        self.repo = ProjectRepo(Path(workspace_root).parent / "agent-projects", workflow_id)
        self.spec = self.repo.load_spec()
        self.socket = socket
        self.id = secrets.token_urlsafe(24)
        self.token = secrets.token_urlsafe(32)
        self.pending = {}
        self.dialogs = {}
        self.transport = None
        self.reader = None
        self.rpc_pending = {}
        self.profile = None
        self.busy = False
        self.agent_running = False
        self.closed = False
        self.model = {}
        self.thinking_level = "off"
        self.ponytail = False
        self.initialized = asyncio.Event()
        self.turns = 0
        self.send_lock = asyncio.Lock()
        config = validate_pi_model_config(config) if config is not None else None
        command, provider, _, _ = _build_command(config, offline=False)
        # The structured-output node forbids action tools. Chat must instead use
        # Pi's native agent prompt, augmented by our trusted canvas/skill sections.
        prompt_index = command.index("--system-prompt")
        del command[prompt_index : prompt_index + 2]
        # Reuse Pi, not the schema-only completion mode. Only our trusted extension
        # is loaded; Pi's native MCP configuration remains available.
        command = [
            arg
            for arg in command
            if arg
            not in {
                "--no-tools",
                "--no-skills",
                "--no-context-files",
                "--no-prompt-templates",
            }
        ]
        # ponytail: per-tab history only; reuse Pi session files if reload
        # continuity is needed. Keeping --no-session avoids unexpected disk data.
        command += [
            "--exclude-tools",
            "bash,powershell,grep,find,ls",
            "--extension",
            "builtin:mcp",
            "--extension",
            "builtin:codemode",
            "--extension",
            "builtin:tool-search",
            "--extension",
            str(Path(__file__).with_name("pi_chat_extension.ts")),
        ]
        environment = _pi_environment()
        environment["COMFY_PI_PARENT_AGENT_DIR"] = environment.get("PI_CODING_AGENT_DIR", "")
        self.auth_source = config["credential_source"] if config else "pi_managed"
        if self.auth_source == "studio_keychain":
            credentials = provider_credential_environment(provider)
            if not credentials:
                raise PiCLIError(
                    "Save a Studio Keychain credential for the selected provider first."
                )
            for field in all_provider_api_key_env_names():
                environment.pop(field, None)
            self.profile = tempfile.TemporaryDirectory(prefix="studio-pi-chat-")
            environment["PI_CODING_AGENT_DIR"] = self.profile.name
            environment.update(credentials)
        environment.update(
            {
                "COMFY_PI_BRIDGE_URL": origin + PREFIX + "/bridge/" + self.id,
                "COMFY_PI_BRIDGE_TOKEN": self.token,
                "COMFY_PI_WORKSPACE_DIR": str(self.workspace.path),
                "COMFY_PI_WORKFLOW_ID": self.workspace.id,
                "COMFY_PI_WORKFLOW_GRAPH_ID": self.graph_id,
            }
        )
        self.command, self.environment = command, environment

    async def start(self):
        # Popen itself is short; assign ownership before yielding so shutdown
        # cannot lose a child created by a cancelled executor future.
        await asyncio.to_thread(self.workspace.perform, "workspace_info", {})
        self.transport = _PiRPCProcess(self.command, self.environment)
        self.reader = asyncio.create_task(self.read_events())
        await self.send({"type": "get_state", "id": "chat_state"})
        await asyncio.wait_for(self.initialized.wait(), timeout=25)

    async def send(self, record):
        async with self.send_lock:
            await asyncio.to_thread(self.transport.send, record)

    async def client_message(self, record):
        kind = record.get("type")
        if kind == "prompt":
            if not self.initialized.is_set():
                raise ValueError("Wait for Pi to finish connecting first.")
            text = record.get("message")
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ValueError("Send 1–16,000 characters.")
            if self.busy:
                raise ValueError("Stop or wait for the current reply first.")
            self.busy, self.agent_running, self.turns = True, False, 0
            await self.send({"type": "prompt", "message": text, "id": secrets.token_hex(8)})
        elif kind == "configure":
            if not self.initialized.is_set() or self.busy:
                raise ValueError("Wait for Pi to finish before changing models.")
            config = validate_pi_model_config(record.get("pi_config"))
            if config["credential_source"] != self.auth_source or (
                self.auth_source == "studio_keychain"
                and config["provider"] != self.model.get("provider")
            ):
                raise ValueError("Start New chat to change the credential profile.")
            await self.rpc(
                {
                    "type": "set_model",
                    "provider": config["provider"],
                    "modelId": config["model"],
                }
            )
            await self.rpc({"type": "set_thinking_level", "level": config["thinking_level"]})
            state = await self.rpc({"type": "get_state"})
            self.model = {key: state["model"].get(key) for key in ("provider", "id")}
            self.thinking_level = state.get("thinkingLevel", "off")
            await self.socket.send_json({"type": "model_config", "pi_config": self.configuration()})
        elif kind in {"workspace_files", "workspace_file"}:
            # Browser-only reads reuse the same bound, confined IO as agent tools.
            response = {"type": kind, "id": record.get("id")}
            try:
                action = "workspace_info" if kind == "workspace_files" else "workspace_read"
                args = {} if kind == "workspace_files" else {"path": record.get("path")}
                response["result"] = await self.workspace_action(action, args)
                if kind == "workspace_file":
                    response["result"]["path"] = args["path"]
            except (ValueError, TypeError, OSError) as error:
                response["error"] = str(error)
            await self.socket.send_json(response)
        elif kind == "abort":
            await self.send({"type": "abort"})
            self.cancel_pending()
        elif kind == "extension_ui_response":
            method = self.dialogs.pop(record.get("id"), None)
            if method is None:
                raise ValueError("Permission request is no longer pending.")
            response = {"type": kind, "id": record["id"]}
            if record.get("cancelled") is True:
                response["cancelled"] = True
            elif method == "confirm" and type(record.get("confirmed")) is bool:
                response["confirmed"] = record["confirmed"]
            elif (
                method in {"input", "editor", "select"}
                and isinstance(record.get("value"), str)
                and len(record["value"]) <= 16000
            ):
                response["value"] = record["value"]
            else:
                raise ValueError("Invalid permission response.")
            await self.send(response)
        elif kind == "comfy_result":
            future = self.pending.get(record.get("id"))
            if future is None or future.done():
                raise ValueError("Canvas action is no longer pending.")
            if record.get("error"):
                future.set_exception(ValueError(str(record["error"])[:2000]))
            else:
                future.set_result(record.get("result"))
        else:
            raise ValueError("Unsupported chat command.")

    def configuration(self):
        return {
            "version": 1,
            "provider": self.model.get("provider"),
            "model": self.model.get("id"),
            "thinking_level": self.thinking_level,
            "credential_source": self.auth_source,
        }

    async def workspace_action(self, action, args):
        result = await asyncio.to_thread(self.workspace.perform, action, args)
        if action == "workspace_write":
            # Notification failure must not misreport a committed file mutation.
            with contextlib.suppress(ConnectionError, RuntimeError):
                await self.socket.send_json({"type": "workspace_saved", "path": args["path"]})
        return result

    async def rpc(self, command):
        id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.rpc_pending[id] = future
        try:
            await self.send({**command, "id": id})
            response = await asyncio.wait_for(future, timeout=25)
            if not response.get("success"):
                raise PiCLIError(str(response.get("error", "Pi configuration failed.")))
            return response.get("data", {})
        finally:
            self.rpc_pending.pop(id, None)

    async def read_events(self):
        try:
            while not self.socket.closed:
                try:
                    event = await asyncio.to_thread(
                        self.transport.next_record, time.monotonic() + 1
                    )
                except TimeoutError:
                    continue
                if event.get("type") == "response" and event.get("id") in self.rpc_pending:
                    future = self.rpc_pending[event["id"]]
                    if not future.done():
                        future.set_result(event)
                    continue
                if event.get("type") == "response" and event.get("id") == "chat_state":
                    if not event.get("success"):
                        raise PiCLIError("Pi could not initialize its session.")
                    state = event.get("data", {})
                    model = state.get("model") or {}
                    self.model = {key: model.get(key) for key in ("provider", "id")}
                    self.thinking_level = state.get("thinkingLevel", "off")
                    await self.send({"type": "get_commands", "id": "chat_extension"})
                    continue
                if event.get("type") == "response" and event.get("id") == "chat_extension":
                    names = {item.get("name") for item in event.get("data", {}).get("commands", [])}
                    if not event.get("success") or "comfy" not in names:
                        raise PiCLIError("Pi could not load its native ComfyUI extension.")
                    # Ponytail is optional; when installed, use its own command, not copied prompt text.
                    self.ponytail = "ponytail" in names
                    await self.send(
                        {"type": "prompt", "id": "chat_ponytail", "message": "/ponytail full"}
                        if self.ponytail
                        else {
                            "type": "prompt",
                            "id": "chat_tool_check",
                            "message": "/comfy --check",
                        }
                    )
                    continue
                if event.get("type") == "response" and event.get("id") == "chat_ponytail":
                    if (
                        not event.get("success")
                        or event.get("data", {}).get("disposition") != "handled"
                    ):
                        raise PiCLIError("Pi could not activate Ponytail full mode.")
                    await self.send(
                        {
                            "type": "prompt",
                            "id": "chat_tool_check",
                            "message": "/comfy --check",
                        }
                    )
                    continue
                if event.get("type") == "extension_ui_request" and event.get("method") == "notify":
                    try:
                        tools = json.loads(event.get("message", ""))
                    except (ValueError, TypeError):
                        tools = None
                    if isinstance(tools, dict) and tools.get("type") == "comfy_tools":
                        if tools.get("schema_only_prompt") is not False:
                            raise PiCLIError(
                                "Pi chat cannot use a structured-output-only system prompt."
                            )
                        required = {
                            "comfy_read_workflow",
                            "comfy_node_catalog",
                            "comfy_edit_workflow",
                            "comfy_run_workflow",
                            "comfy_execution_results",
                        }
                        if not required.issubset(tools.get("active", [])):
                            raise PiCLIError(
                                "Pi's native canvas tools are not active; check its tool configuration."
                            )
                        extensions = tools.get("extensions", {})
                        if (
                            not self.initialized.is_set()
                            and self.ponytail
                            and extensions.get("ponytail", {}).get("mode") != "full"
                        ):
                            raise PiCLIError("Ponytail full mode was not verified.")
                        skills = tools.get("skills", {})
                        if (
                            not skills.get("default_loaded")
                            or set(skills.get("names", [])) != REQUIRED_SKILLS
                        ):
                            raise PiCLIError("Default ComfyUI skills were not verified.")
                        workspace = tools.get("workspace", {})
                        if (
                            workspace.get("workflow_id") != self.workspace.id
                            or workspace.get("graph_id") != self.graph_id
                            or workspace.get("root") != str(self.workspace.path)
                            or workspace.get("shell_enabled") is not False
                            or workspace.get("file_tools_scoped") is not True
                        ):
                            raise PiCLIError("Confined workflow file tools were not verified.")
                        await self.socket.send_json(
                            {
                                "type": "extensions_status"
                                if self.initialized.is_set()
                                else "ready",
                                "extensions": extensions,
                                "skills": skills,
                                "schema_only_prompt": False,
                                "workspace_files_ui": True,
                                "workspace": workspace,
                                "model": self.model,
                                "auth_source": self.auth_source,
                                "pi_config": self.configuration(),
                                "mcp_tool_count": len(tools.get("mcp", [])),
                            }
                        )
                        self.initialized.set()
                        continue
                if event.get("type") == "response" and event.get("id") in {
                    "chat_tool_check",
                    "chat_status",
                }:
                    if not event.get("success"):
                        raise PiCLIError("Pi could not check its active native tools.")
                    continue
                if event.get("type") == "agent_start":
                    self.agent_running = True
                    if not self.busy:
                        self.busy = True
                        self.turns = 0
                if (
                    event.get("type") == "response"
                    and event.get("data", {}).get("disposition") == "handled"
                    and not self.agent_running
                ):
                    self.busy = False
                    await self.send(
                        {
                            "type": "prompt",
                            "id": "chat_status",
                            "message": "/comfy --check",
                        }
                    )
                    await self.socket.send_json({"type": "command_handled"})
                if event.get("type") == "response" and event.get("success") is False:
                    self.busy = False
                    self.cancel_pending()
                if event.get("type") == "turn_start":
                    self.turns += 1
                    if self.turns > 16:
                        await self.send({"type": "abort"})
                        await self.socket.send_json(
                            {
                                "type": "chat_error",
                                "error": "Stopped after 16 agent turns. Refine the request to continue.",
                            }
                        )
                if event.get("type") == "agent_settled":
                    self.busy = self.agent_running = False
                    self.cancel_pending()
                if event.get("type") == "extension_ui_request" and event.get("method") in {
                    "confirm",
                    "select",
                    "input",
                    "editor",
                }:
                    self.dialogs[event["id"]] = event["method"]
                visible = public_event(event)
                if visible is not None:
                    await self.socket.send_json(visible)
        except (PiCLIError, ConnectionError, RuntimeError) as error:
            if not self.socket.closed:
                await self.socket.send_json({"type": "chat_error", "error": str(error)[:2000]})
                await self.socket.close()

    def cancel_pending(self):
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.dialogs.clear()

    async def agent_action(self, action, arguments):
        """Everything the agent's tools ask of the session, with the plan gate enforced here."""
        if action in FILE_ACTIONS:
            return await self.workspace_action(action, arguments)
        if action in SPEC_ACTIONS:
            return await self.spec_action(action, arguments)
        if action in {"spec_approve", "gates"}:
            raise ValueError("Use comfy_workflow_spec or comfy_check_gates.")
        operations = arguments.get("operations") if isinstance(arguments, dict) else None
        if (
            action == "edit"
            and self.spec is None
            and any(isinstance(op, dict) and op.get("op") == "add" for op in operations or [])
        ):
            raise ValueError(
                "Adding nodes needs an approved workflow plan. Clarify the requirements with "
                "comfy_ask_user, then propose the plan with comfy_workflow_spec."
            )
        return await self.canvas_action(action, arguments)

    async def spec_action(self, action, arguments):
        if action == "spec_get":
            return {"spec": self.spec}
        if action == "spec_gates":
            # Gates come from the approved plan, never from the agent's arguments.
            if self.spec is None:
                raise ValueError("This workflow has no approved plan yet.")
            gates = [
                {
                    "step": s["id"],
                    "description": s["success_gate"]["description"],
                    "check": s["success_gate"].get("check"),
                }
                for s in self.spec["steps"]
            ]
            return await self.canvas_action("gates", {"gates": gates})
        spec = validate_spec(arguments.get("spec") if isinstance(arguments, dict) else None)
        # Approval is the user's click in the browser, never the agent's own tool call.
        answer = await self.canvas_action(
            "spec_approve", {"title": "Approve this workflow plan?", "text": render_spec(spec)}
        )
        if not answer.get("approved"):
            return {
                "approved": False,
                "message": "The user did not approve the plan; ask what to change.",
            }
        sha = await asyncio.to_thread(self.repo.save_spec, spec)
        self.spec = spec
        return {"approved": True, "version": sha[:8]}

    async def canvas_action(self, action, arguments):
        if action not in ACTIONS or not isinstance(arguments, dict):
            raise ValueError("Invalid canvas action.")
        if len(self.pending) >= 8:
            raise ValueError("Too many pending canvas actions.")
        id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.pending[id] = future
        try:
            await self.socket.send_json(
                {"type": "comfy_action", "id": id, "action": action, "args": arguments}
            )
            return await future
        finally:
            self.pending.pop(id, None)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_pending()
        for future in self.rpc_pending.values():
            if not future.done():
                future.cancel()
        for task in (self.reader,):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.transport:
            await asyncio.to_thread(self.transport.abort)
        if self.profile:
            self.profile.cleanup()


def install_pi_chat_routes():
    from server import PromptServer

    server = PromptServer.instance
    sessions = {}
    sockets = set()

    @server.routes.get(PREFIX + "/ws")
    async def chat(request):
        local_request(request, browser=True)
        if len(sockets) >= 2:
            raise web.HTTPTooManyRequests(text="Close another Pi chat before starting a new one.")
        socket = web.WebSocketResponse(max_msg_size=MAX_MESSAGE, heartbeat=30)
        sockets.add(socket)
        session = None
        boot = None

        async def bootstrap(session):
            try:
                await session.start()
            except (ValueError, TypeError, PiCLIError, OSError, TimeoutError) as error:
                if not socket.closed:
                    await socket.send_json(
                        {
                            "type": "chat_error",
                            "error": str(error)[:2000] or "Pi startup timed out.",
                        }
                    )
                    await socket.close()

        try:
            await socket.prepare(request)
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    record = json.loads(message.data)
                    if not isinstance(record, dict):
                        raise TypeError("Chat message must be an object.")
                    if session is None:
                        if record.get("type") != "start" or set(record) - {
                            "type",
                            "pi_config",
                            "workflow_id",
                        }:
                            raise ValueError("Start the Pi session first.")
                        if len(sessions) >= 2:
                            raise ValueError("Two Pi sessions are already open.")
                        session = PiChatSession(
                            socket,
                            f"{request.scheme}://{request.host}",
                            record.get("pi_config"),
                            workflow_id=record.get("workflow_id"),
                        )
                        sessions[session.id] = session
                        boot = asyncio.create_task(bootstrap(session))
                    else:
                        await session.client_message(record)
                except (
                    ValueError,
                    TypeError,
                    PiCLIError,
                    OSError,
                    TimeoutError,
                ) as error:
                    await socket.send_json(
                        {
                            "type": "chat_error",
                            "error": str(error)[:2000] or "Pi startup timed out.",
                        }
                    )
                    if session is not None and not session.initialized.is_set():
                        await socket.close()
                        break
        finally:
            sockets.discard(socket)
            if boot:
                boot.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await boot
            if session:
                sessions.pop(session.id, None)
                await session.close()
        return socket

    @server.routes.post(PREFIX + "/bridge/{session_id}")
    async def bridge(request):
        local_request(request)
        session = sessions.get(request.match_info["session_id"])
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if session is None or not secrets.compare_digest(token.encode(), session.token.encode()):
            raise web.HTTPForbidden(text="Invalid Pi bridge session.")
        if request.content_type != "application/json":
            raise web.HTTPBadRequest(text="JSON required.")
        body = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            body.extend(chunk)
            if len(body) > 262144:
                raise web.HTTPRequestEntityTooLarge(max_size=262144, actual_size=len(body))
        try:
            record = json.loads(body)
            if not isinstance(record, dict) or set(record) != {"action", "args"}:
                raise ValueError("Invalid bridge payload.")
            result = await session.agent_action(record["action"], record["args"])
            return web.json_response({"result": result})
        except (ValueError, TypeError, TimeoutError, RuntimeError) as error:
            return web.json_response(
                {"error": str(error) or "Canvas action timed out."}, status=400
            )
        except asyncio.CancelledError:
            return web.json_response({"error": "Canvas action cancelled."}, status=409)

    async def shutdown(app):
        await asyncio.gather(*(socket.close() for socket in list(sockets)), return_exceptions=True)
        await asyncio.gather(
            *(session.close() for session in list(sessions.values())),
            return_exceptions=True,
        )

    server.app.on_shutdown.append(shutdown)
