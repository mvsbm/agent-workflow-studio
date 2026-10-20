"""Native Pi RPC sessions; browser owns the canvas, ComfyUI owns execution."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import itertools
import json
import logging
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from . import api_review
from . import loop as improve_loop
from .agent_tools import NAME, approval_text, code_hash
from .lld import validate_lld
from .native_layers import design_of, intent_of, layers, split
from .pi_auth import (
    all_provider_api_key_env_names,
    provider_credential_environment,
)
from .pi_chat_workspace import FILE_ACTIONS, PiChatWorkspace, workspace_id_for_graph
from .pi_cli import PiCLIError, _build_command, _pi_environment, _PiRPCProcess
from .pi_model_config import validate_pi_model_config
from .project_repo import ProjectRepo
from .workflow_contract import (
    blocking,
    hld,
    join,
    render,
    report,
    review,
    validate_for_approval,
    validate_intent,
    wiring,
)

PREFIX = "/agent-workflow-studio/pi-chat"
ACTIONS = {
    "intent",
    "build",
    "read",
    "catalog",
    "edit",
    "run",
    "results",
    "contract_approve",
    "tool_approve",
    "contract_evidence",
    "gates",
    "skeleton",
    "harden",
    "examples",
    "analyze",
    "converters",
}
# Edit operations that change what runs; after them the graph is checked automatically.
STRUCTURAL = {"add", "remove", "connect", "disconnect"}
CONTRACT_ACTIONS = {
    "contract_propose",
    "design_propose",
    "build_step",
    "contract_get",
    "contract_gates",
    "contract_status",
    "tool_propose",
    "loop_step",
}
# Ponytail ultra also questions the request before building, which suits planning first.
PONYTAIL_MODE = "ultra"
# Minimal correct shapes, shown when a proposal's shape is wrong (a model fixes a shape best by example).
CONTRACT_SHAPE = (
    '{"contract": {"contract": 2, "purpose": "…", "requests": [{"id": "R1", "title": "…", "you_said": "…", '
    '"we_understood": "When …, the workflow …", "priority": "must"}], "examples": [{"id": "E1", "covers": '
    '["R1"], "given": {"orders": "orders_sample"}, "expect": {"totals": "north"}}], "promises": {"inputs": '
    '[{"name": "orders", "kind": "folder", "description": "…", "example": "orders_sample"}], "outputs": '
    '[{"name": "totals", "kind": "table", "description": "…"}]}, "decisions": [], "not_now": [], '
    '"open_questions": []}}'
)
DESIGN_SHAPE = (
    '{"design": {"steps": [{"id": "total", "purpose": "…", "approach": "fixed", "covers": ["R1"], '
    '"takes": {"orders": "input.orders"}, "gives": {"totals": "TABLE"}, "uses": "Read table, Summarize", '
    '"why": "…", "pattern": "Pipes and filters", "gate": {"says": "…", "check": {"output": "totals", '
    '"kind": "rows_at_least", "value": 1}}}], "data": {"totals": {"description": "…", "key": ["region"], '
    '"fields": ["region", "revenue"]}}, "decisions": [{"id": "D2", "decision": "…", "why": "…", "by": '
    '"agent"}], "outputs": {"totals": "total.totals"}}}'
)


def as_object(value: object) -> object:
    """A tool argument as meant: JSON text parsed (models sometimes send objects as text), and
    optional fields sent as null dropped (null means "not given")."""
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        return {k: as_object(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [as_object(v) for v in value]
    return value


def renumbered(design: dict, intent: dict) -> dict:
    """A first design's own decisions renumbered after the contract's (they share one numbering).
    Only before anything is built: no LLD can cite them yet."""
    taken = {d.get("id") for d in intent.get("decisions") or [] if isinstance(d, dict)}
    numbers = [int(i[1:]) for i in taken if isinstance(i, str) and i[1:].isdigit()]
    next_free, out = max(numbers, default=0) + 1, []
    for d in design.get("decisions") or []:
        if isinstance(d, dict) and d.get("id") in taken:
            d = {**d, "id": f"D{next_free}"}
            next_free += 1
        out.append(d)
    return {**design, "decisions": out} if "decisions" in design else design


def shape_hint(problems: list[str], shape: str) -> str:
    """A minimal correct example, when a problem is about the shape itself."""
    return (
        f" Expected shape, e.g. {shape}"
        if any("Invalid" in p or "Malformed" in p for p in problems)
        else ""
    )


REQUIRED_SKILLS = {
    "comfyui-node-creation",
    "comfyui-workflow-debugging",
    "comfyui-connector-management",
    "comfyui-node-organization",
    "comfyui-workflow-contracts",
    "comfyui-design-patterns",
    "comfyui-api-design",
}
MAX_MESSAGE = 1_048_576
# Above this a tool is doing several stages at once (counted without blank lines).
MAX_TOOL_LINES = 500
# Agent turns per user message: room for a contract-gated multi-step build, still a loop guard.
MAX_TURNS = 60


def _decode(head: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp932"):
        for cut in range(4):  # the preview may end inside a multi-byte character
            try:
                return head[: len(head) - cut].decode(encoding), encoding
            except UnicodeDecodeError:
                continue
    return head.decode("latin-1"), "unknown (shown as latin-1)"


def input_files(base: Path, path: str = "") -> dict:
    """Read-only look at ComfyUI's input folder: a listing, or the first lines of a text file."""
    base = base.resolve()
    target = (base / path).resolve()
    if base not in (target, *target.parents):
        raise ValueError("Only files inside ComfyUI's input directory can be viewed.")
    if target.is_dir():
        files = [p for p in itertools.islice(target.rglob("*"), 2000) if p.is_file()]
        return {
            "folder": str(target.relative_to(base)),
            "files": [
                {"path": str(p.relative_to(base)), "bytes": p.stat().st_size}
                for p in sorted(files)[:200]
            ],
        }
    if not target.is_file():
        raise ValueError("No such file in ComfyUI's input directory.")
    with open(target, "rb") as file:
        head = file.read(32768)
        if b"\0" in head:
            raise ValueError("Binary file: only text files can be previewed.")
        lines = head.count(b"\n") + sum(
            chunk.count(b"\n") for chunk in iter(lambda: file.read(1 << 20), b"")
        )
    text, encoding = _decode(head)
    return {
        "path": str(target.relative_to(base)),
        "encoding": encoding,
        "bytes": target.stat().st_size,
        "lines": lines,
        "head": text.splitlines()[:60],
    }


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
        self.contract = self.repo.load_contract()
        self.loop_pending: dict | None = None  # the improve-loop iteration in progress
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
        self.launch(config, origin)

    def bridge_environment(self, origin):
        """How the agent's tools reach this session; inherited by the agent's tool process, never in argv."""
        return {
            "COMFY_PI_BRIDGE_URL": origin + PREFIX + "/bridge/" + self.id,
            "COMFY_PI_BRIDGE_TOKEN": self.token,
            "COMFY_PI_WORKSPACE_DIR": str(self.workspace.path),
            "COMFY_PI_WORKFLOW_ID": self.workspace.id,
            "COMFY_PI_WORKFLOW_GRAPH_ID": self.graph_id,
        }

    def launch(self, config, origin):
        """The agent process's command and environment (Pi here; claude_chat.py for Claude Code)."""
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
        environment.update(self.bridge_environment(origin))
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
            await self.answer_dialog(response)
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

    async def answer_dialog(self, response):
        await self.send(response)

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
                        {
                            "type": "prompt",
                            "id": "chat_ponytail",
                            "message": f"/ponytail {PONYTAIL_MODE}",
                        }
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
                        raise PiCLIError(f"Pi could not activate Ponytail {PONYTAIL_MODE} mode.")
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
                            and extensions.get("ponytail", {}).get("mode") != PONYTAIL_MODE
                        ):
                            raise PiCLIError(f"Ponytail {PONYTAIL_MODE} mode was not verified.")
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
                    if self.turns > MAX_TURNS:
                        await self.send({"type": "abort"})
                        await self.socket.send_json(
                            {
                                "type": "chat_error",
                                "error": f"Stopped after {MAX_TURNS} agent turns. Say continue to go on.",
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
            logging.warning("Pi chat ended: %s", error)
            if not self.socket.closed:
                await self.socket.send_json({"type": "chat_error", "error": str(error)[:2000]})
                await self.socket.close()

    def cancel_pending(self):
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.dialogs.clear()

    async def agent_action(self, action, arguments):
        """Everything the agent's tools ask of the session, with the contract gate enforced here."""
        if action in FILE_ACTIONS:
            path = arguments.get("path") if isinstance(arguments, dict) else None
            if (
                action == "workspace_write"
                and self.contract is None
                and not str(path).startswith("tmp/")
            ):
                raise ValueError(
                    "Writing workspace files needs an approved contract (scratch files may go in tmp/). "
                    "Plan first: comfy_ask_user, then comfy_propose_contract."
                )
            return await self.workspace_action(action, arguments)
        if action in CONTRACT_ACTIONS:
            return await self.contract_action(action, arguments)
        if action == "input_files":
            import folder_paths

            path = arguments.get("path") if isinstance(arguments, dict) else None
            return input_files(Path(folder_paths.get_input_directory()), str(path or ""))
        if action in {"contract_approve", "tool_approve", "contract_evidence", "gates", "skeleton"}:
            raise ValueError(
                "Use comfy_propose_contract, comfy_check_gates or comfy_contract_status."
            )
        operations = arguments.get("operations") if isinstance(arguments, dict) else None
        structural = action == "edit" and any(
            isinstance(op, dict) and op.get("op") in STRUCTURAL for op in operations or []
        )
        if action == "edit" and any(
            isinstance(op, dict) and op.get("op") in STRUCTURAL and op.get("stage") is not None
            for op in operations or []
        ):
            # Inside a stage the nodes come from its low-level design, never from loose edits.
            raise ValueError(
                "Inside a stage, nodes and wires come from its LLD: build or change the stage with "
                "comfy_build_step (its nodes, what each realizes, its wires). Settings (widget), titles "
                "and positions inside a stage may still be edited."
                if self.phase() == "build"
                else "Nodes are built once the contract and its design are approved: comfy_ask_user, "
                "comfy_propose_contract, then comfy_propose_design."
            )
        if (
            action == "edit"
            and self.contract is None
            and any(isinstance(op, dict) and op.get("op") == "add" for op in operations or [])
        ):
            raise ValueError(
                "Adding nodes needs an approved contract. Clarify the requirements with "
                "comfy_ask_user, then propose the contract with comfy_propose_contract."
            )
        if action == "edit" and self.contract is not None:
            # The approved design's wiring: the canvas refuses top-level edits that contradict it.
            arguments = {**arguments, "design": wiring(self.contract)}
        outside = (
            action == "edit"
            and self.contract is not None
            and not self.loop_pending
            and any(
                isinstance(op, dict) and op.get("op") in STRUCTURAL | {"widget"}
                for op in operations or []
            )
            and await asyncio.to_thread(self.repo.records, "loop")
        )
        if outside:
            # Improving, but outside an iteration: allowed, never hidden (the version says so).
            summary = str(arguments.get("summary") or "canvas edit")
            arguments = {**arguments, "summary": (improve_loop.OUTSIDE + summary)[:200]}
        result = await self.canvas_action(action, arguments)
        if outside:
            result = {
                **result,
                "outside_loop": "This change is not part of an improve-loop iteration, so it is not "
                "measured; the version and the Contract tab say so. Change a built workflow through "
                "comfy_loop_step (hypothesis + the change), then run and check gates.",
            }
        if structural:
            result = {**result, "graph_check": await self.check_graph()}
        return result

    async def check_graph(self):
        """The automatic check after a structural change: problems the agent must see, and the user too."""
        analysis = await self.canvas_action("analyze", {})
        problems = [p["message"] for p in analysis["problems"]]
        idle = [n["title"] for n in analysis["will_not_run"]]
        lines = [f"Graph check: {analysis['summary']}"]
        lines += [f"• {p}" for p in problems[:8]]
        if idle:
            lines.append("• Will not run (reaches no output): " + ", ".join(idle[:8]))
        result = {"summary": analysis["summary"], "problems": problems, "will_not_run": idle}
        if self.contract is not None:
            evidence = await self.canvas_action("contract_evidence", {})
            status = report(self.live(evidence), evidence)
            result["contract"] = {
                "aligned": status["aligned"],
                "drift": status["drift"],
                "progress": status["progress"],
                "todo": status["todo"],
            }
            lines.append(
                "Contract: ✓ aligned · " + status["progress"]
                if status["aligned"]
                else f"Contract: ⚠ {len(status['drift'])} drift(s) · {status['progress']}"
            )
            lines += [f"• {d}" for d in status["drift"][:6]]
            if status["todo"]:
                lines.append(f"To do ({len(status['todo'])}): " + "; ".join(status["todo"][:4]))
        await self.socket.send_json({"type": "graph_check", "text": "\n".join(lines)})
        return result

    async def contract_action(self, action, arguments):
        if action == "contract_get":
            return {"contract": self.contract}
        if action != "contract_propose" and self.contract is None:
            raise ValueError(
                "This workflow has no approved contract yet: propose it first with comfy_propose_contract."
            )
        if action == "contract_gates":
            # Gates come from the approved contract, never from the agent's arguments.
            gates = await self.canvas_action("gates", {})
            evidence = await self.canvas_action("contract_evidence", {})
            accepted = await asyncio.to_thread(self.repo.records, "acceptance")
            trace = report(self.live(evidence), evidence, accepted[-1] if accepted else None)[
                "trace"
            ]
            unproven = [
                r["id"]
                for r in trace["requests"]
                if r["priority"] == "must" and r["verdict"] != "proven"
            ]
            return {
                **gates,
                "loop": await self.loop_state(evidence),
                # Passing gates are not the user's requests met: say what is still unproven.
                "plan_vs_built": trace["summary"],
                "gaps": [
                    f"[{g['severity']}] {g['where']}: {g['text']}" for g in trace["gaps"][:15]
                ],
                **(
                    {
                        "before_reporting": f"Must-haves {', '.join(unproven)} are not proven on this "
                        "version: run comfy_run_examples, and report any that still fail or have no example."
                    }
                    if unproven
                    else {}
                ),
            }
        if action == "loop_step":
            return await self.start_iteration(arguments if isinstance(arguments, dict) else {})
        if action == "tool_propose":
            return await self.propose_tool(arguments if isinstance(arguments, dict) else {})
        if action == "contract_status":
            evidence = await self.canvas_action("contract_evidence", {})
            accepted = await asyncio.to_thread(self.repo.records, "acceptance")
            status = report(self.live(evidence), evidence, accepted[-1] if accepted else None)
            loop = await self.loop_state(evidence)
            examples = (self.contract or {}).get("examples") or []
            if examples and not (accepted and accepted[-1]["passed"] == accepted[-1]["total"]):
                status["todo"] = [
                    "Run the contract's examples as acceptance tests (comfy_run_examples)",
                    *status["todo"],
                ]
            return {
                **{
                    k: status[k]
                    for k in ("progress", "freshness", "steps", "nodes_outside_steps", "todo")
                },
                # The same honest comparison the user sees: report these gaps, never claim past them.
                "plan_vs_built": status["trace"]["summary"],
                "gaps": [
                    f"[{g['severity']}] {g['where']}: {g['text']}"
                    for g in status["trace"]["gaps"][:15]
                ],
                "loop": loop,
            }
        if action == "design_propose":
            return await self.propose_design(arguments if isinstance(arguments, dict) else {})
        if action == "build_step":
            return await self.build_step(arguments if isinstance(arguments, dict) else {})
        return await self.propose_contract(arguments if isinstance(arguments, dict) else {})

    def phase(self) -> str:
        """Where the work stands, and so which tools apply: contract, then design (HLD), then build."""
        if self.contract is None:
            return "contract"
        return "build" if self.contract["steps"] else "design"

    async def approve(self, title: str, text: str) -> bool:
        # Approval is the user's click in the browser, never the agent's own tool call.
        answer = await self.canvas_action("contract_approve", {"title": title, "text": text})
        return bool(answer.get("approved"))

    async def commit(self, message: str) -> str:
        """Commit the canvas (which now holds the approved layers natively) as the user's approval."""
        workflow = (await self.canvas_action("contract_evidence", {}))["workflow"]
        return await asyncio.to_thread(self.repo.commit_contract, workflow, message)

    async def propose_contract(self, arguments: dict) -> dict:
        """The contract: what the user wants (intent only). Its design comes next, approved on its own."""
        # Every problem in one answer (shape, consistency and the API standard): one retry fixes all.
        intent, problems = as_object(arguments.get("contract")), []
        try:
            intent = validate_intent(intent)
        except ValueError as error:
            problems.append(str(error))
        try:
            problems += [item for ok, item in api_review.contract_rules(intent) if not ok]
        except (AttributeError, KeyError, TypeError):
            pass  # too broken to review yet; the problems above say why
        if problems:
            raise ValueError(
                "Fix before proposing — "
                + " | ".join(problems)
                + shape_hint(problems, CONTRACT_SHAPE)
            )
        design = design_of(self.contract) if self.contract else None
        if design and design["steps"]:
            # An amended contract must still fit the approved design (else amend the design too).
            validate_for_approval(join(intent, design))
        if not await self.approve("Approve this contract?", render(join(intent, {"steps": []}))):
            return {
                "approved": False,
                "message": "The user did not approve the contract; ask what to change.",
            }
        await self.canvas_action("intent", {"intent": intent})
        sha = await self.commit(f"Contract approved: {intent['purpose']}")
        self.contract = join(intent, design or {"steps": []})
        return {
            "approved": True,
            "version": sha[:8],
            "next": "Design it: find the nodes (comfy_node_catalog), then propose the high-level design "
            "with comfy_propose_design (steps with typed ports, gates, data entities, and which step "
            "gives each promised output).",
        }

    async def propose_design(self, arguments: dict) -> dict:
        """The high-level design (HLD): the abstracted workflow. On approval it becomes the stages."""
        # The design, or its fields sent without the wrapper (a common slip; the fields are the same).
        design = as_object(arguments.get("design", arguments if "steps" in arguments else None))
        if isinstance(design, dict) and self.phase() == "design":
            design = renumbered(design, intent_of(self.contract))
        if not isinstance(design, dict):
            # Say what arrived (its type and keys, not its values), so the next try can be right.
            got = type(arguments.get("design")).__name__ if "design" in arguments else "no design"
            raise ValueError(
                f"Give the design as an object (received {got}; arguments had: {', '.join(sorted(arguments)) or 'nothing'})."
                + shape_hint(["Invalid"], DESIGN_SHAPE)
            )
        # Every problem in one answer: shape and consistency, then the design review (incl. the API standard).
        problems, contract = [], None
        try:
            joined = join(intent_of(self.contract), design)
            contract = validate_for_approval(joined)
        except (ValueError, AttributeError, KeyError, TypeError) as error:
            problems.append(
                str(error) if isinstance(error, ValueError) else f"Malformed design: {error}"
            )
            joined = None
        try:
            problems += blocking(contract or joined)
        except (AttributeError, KeyError, TypeError):
            pass  # too broken to review yet; the problems above say why
        if problems:
            raise ValueError(
                "Design review: fix before proposing — "
                + " | ".join(problems)
                + ". Name each port that hands data to another step after its entity in `data`; give every "
                "promised output its step in `outputs` and use every promised input; keep purpose, uses and why short."
                + shape_hint(problems, DESIGN_SHAPE)
            )
        # The user approved the contract already: the card leads with the design, not the same text again.
        text = render(contract)
        text = f"FOR: {contract['purpose']}\n\n{text[text.index('DESIGN (') :]}"
        if not await self.approve("Approve this design?", text):
            return {
                "approved": False,
                "message": "The user did not approve the design; ask what to change.",
            }
        self.contract = contract
        # The approved design becomes the canvas: a typed, wired stage per step, the API's inputs and
        # outputs at the top level (the App), each step stored on its stage.
        intent, meta, _ = split(contract)
        skeleton = await self.canvas_action(
            "skeleton",
            {
                "steps": contract["steps"],
                "promises": contract["promises"],
                "design": hld(contract),
                "intent": intent,
                "meta": meta,
            },
        )
        sha = await self.commit(f"Design approved: {len(contract['steps'])} steps")
        return {
            "approved": True,
            "version": sha[:8],
            "design_review": [f"{'✓' if ok else '✗'} {item}" for ok, item in review(contract)],
            "skeleton": skeleton,
            "next": "Build each stage from its low-level design with comfy_build_step: its nodes, "
            "what each realizes, and its wires.",
        }

    async def build_step(self, arguments: dict) -> dict:
        """The low-level design (LLD) of one step, checked against its HLD and compiled into its stage."""
        if self.phase() != "build":
            raise ValueError(
                "Build stages once the design is approved: comfy_propose_contract, then comfy_propose_design."
            )
        arguments = {
            **arguments,
            **{k: as_object(arguments[k]) for k in ("nodes", "wires") if k in arguments},
        }
        step = next((s for s in self.contract["steps"] if s["id"] == arguments.get("step")), None)
        if step is None:
            ids = ", ".join(s["id"] for s in self.contract["steps"])
            raise ValueError(f"No step {arguments.get('step')!r} in the design (steps: {ids}).")
        agreed = [d["id"] for d in self.contract["decisions"] if d.get("by") != "user"]
        spec = validate_lld(step, arguments, agreed)
        result = await self.canvas_action(
            "build",
            {
                **spec,
                "summary": str(arguments.get("summary") or f"Build {step['id']} from its LLD")[
                    :200
                ],
            },
        )
        return {**result, "graph_check": await self.check_graph()}

    def live(self, evidence: dict) -> dict:
        """The contract the canvas holds now (native layers), else the one approved in this chat."""
        return layers(evidence.get("workflow")) or self.contract

    async def measure(self, evidence: dict | None = None) -> tuple[dict, dict]:
        """The loop's measured state now (and the evidence it came from)."""
        evidence = evidence or await self.canvas_action("contract_evidence", {})
        acceptance = await asyncio.to_thread(self.repo.records, "acceptance")
        # Examples that never ran count as not passing: the goal includes them.
        examples = len((self.contract or {}).get("examples") or [])
        last = (
            acceptance[-1]
            if acceptance
            else ({"passed": 0, "total": examples} if examples else None)
        )
        state = improve_loop.snapshot(report(self.live(evidence), evidence), last)
        return state, evidence

    async def start_iteration(self, arguments: dict) -> dict:
        """Begin one improve-loop iteration: what the agent will change and why; the studio measures."""
        hypothesis = str(arguments.get("hypothesis", "")).strip()[:300]
        change = str(arguments.get("change", "")).strip()[:300]
        if not hypothesis or not change:
            raise ValueError(
                "Say the hypothesis (why this should help) and the change (which stage/node)."
            )
        if self.loop_pending:
            raise ValueError(
                f"Iteration {self.loop_pending['n']} is still open: run the workflow and check the gates to close it first."
            )
        journal = await asyncio.to_thread(self.repo.records, "loop")
        state, _ = await self.measure()
        plan = improve_loop.next_action(journal, state, improve_loop.budget_of(self.contract))
        if plan["next"] in ("stop", "escalate"):
            return {"started": False, **plan, "journal": improve_loop.summary(journal)}
        self.loop_pending = {
            "n": len(journal) + 1,
            "hypothesis": hypothesis,
            "change": change,
            "before": state,
            "version_before": await asyncio.to_thread(self.repo.head),
        }
        return {
            "started": True,
            "iteration": self.loop_pending["n"],
            "before": state,
            "journal": improve_loop.summary(journal),
            "how": "Make this one change, run, then check the gates: the studio measures the result.",
        }

    async def loop_state(self, evidence: dict | None = None) -> dict:
        """Close an open iteration once a run of the changed graph has been measured; say what's next."""
        journal = await asyncio.to_thread(self.repo.records, "loop")
        state, evidence = await self.measure(evidence)
        closed = None
        if self.loop_pending and state["current"]:
            entry = {
                "kind": "loop",
                **self.loop_pending,
                "after": state,
                "verdict": improve_loop.verdict(self.loop_pending["before"], state),
            }
            message = f"Loop {entry['n']} · {entry['verdict']}: {entry['hypothesis']}"
            await asyncio.to_thread(self.repo.add_record, entry, message, evidence["workflow"])
            journal.append(entry)
            closed, self.loop_pending = entry, None
        outside = improve_loop.outside_loop(await asyncio.to_thread(self.repo.log))
        return {
            **({"outside_loop": outside} if outside else {}),
            **(
                {"closed": {k: closed[k] for k in ("n", "verdict", "hypothesis")}} if closed else {}
            ),
            **({"open": self.loop_pending["n"]} if self.loop_pending else {}),
            **improve_loop.next_action(journal, state, improve_loop.budget_of(self.contract)),
            "journal": improve_loop.summary(journal)[-6:],
        }

    async def propose_tool(self, arguments):
        """Show the user the exact code of a workspace tool; on approval, store it in the project."""
        name = arguments.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("A tool name is a short lowercase identifier, e.g. match_names.")
        ports = {key: arguments.get(key) for key in ("inputs", "outputs")}
        if not all(
            isinstance(v, list)
            and all(isinstance(p, dict) and {"name", "kind"} <= p.keys() for p in v)
            for v in ports.values()
        ):
            raise ValueError("Describe inputs and outputs as lists of {name, kind}.")
        description = str(arguments.get("description", "")).strip()[:1200]
        if not description:
            raise ValueError("Say what the tool does.")
        file = await self.workspace_action("workspace_read", {"path": f"tools/{name}.py"})
        code = base64.b64decode(file["data"]).decode("utf-8")
        if "def run(" not in code:
            raise ValueError(f"tools/{name}.py must define run(inputs) returning a dict.")
        size = sum(1 for line in code.splitlines() if line.strip())
        if size > MAX_TOOL_LINES:
            # One giant tool hides the whole job in one node; the canvas should show the stages.
            raise ValueError(
                f"tools/{name}.py has {size} code lines (limit {MAX_TOOL_LINES}). Split it into one "
                "tool per stage, chained through `data`; a later tool reads earlier tools' files "
                "from inputs['_run_folders']."
            )
        text = approval_text(name, description, ports["inputs"], ports["outputs"], code)
        answer = await self.canvas_action(
            "tool_approve", {"title": f"Approve tool {name}?", "text": text}
        )
        if not answer.get("approved"):
            return {
                "approved": False,
                "message": "The user did not approve this tool; ask what to change.",
            }
        manifest = {"name": name, "description": description, **ports, "sha256": code_hash(code)}
        sha = await asyncio.to_thread(self.repo.save_tool, name, code, manifest)
        return {
            "approved": True,
            "version": sha[:8],
            "next": f"Add an Agent Tool node with tool={name!r}; connect its inputs and run.",
        }

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
                        config = record.get("pi_config")
                        kind = PiChatSession
                        if isinstance(config, dict) and config.get("agent") == "claude_code":
                            from .claude_chat import ClaudeChatSession as kind
                        session = kind(
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
            # Why a chat ended is otherwise invisible (heartbeat, oversize message, Pi exit).
            logging.info(
                "Pi chat closed: socket code %s, exception %r",
                socket.close_code,
                socket.exception(),
            )
            if boot:
                boot.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await boot
            if session:
                sessions.pop(session.id, None)
                await session.close()
        return socket

    @server.routes.get("/agent-workflow-studio/claude/status")
    async def claude_status(request):
        from .claude_chat import login_status

        local_request(request)
        return web.json_response(await asyncio.to_thread(login_status))

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
