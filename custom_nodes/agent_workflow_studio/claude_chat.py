"""Local Claude Code as the chat agent, instead of Pi (chosen per workflow in the chat's model picker).

Claude Code runs headless (`claude -p`, stream-json in and out) on the user's own Claude login,
locked down: no built-in tools (no shell, no host files), none of the user's settings, hooks,
plugins or CLAUDE.md, and only the studio's tools from claude_mcp.py. The one exception, by the
user's choice: their installed Ponytail plugin, so its commands work as in the Pi chat (its hooks
run on this machine; it starts in the Pi chat's mode). The studio's tools reach this session's
bridge, so the contract gate, approvals and workspace confinement are the Pi session's, unchanged.
Its stream events are translated into the events the chat panel already shows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .pi_chat import MAX_TURNS, PONYTAIL_MODE, REQUIRED_SKILLS, PiChatSession
from .pi_cli import PiCLIError, _pi_environment, _PiRPCProcess

HERE = Path(__file__).resolve().parent
MODELS = {"default", "sonnet", "opus", "haiku"}
EFFORTS = {"default", "low", "medium", "high", "xhigh", "max"}
PREFIX = "mcp__studio__"


def claude_cli() -> str | None:
    return os.environ.get("AGENT_WORKFLOW_STUDIO_CLAUDE_CLI") or shutil.which("claude")


def ponytail_plugin() -> tuple[str, str] | None:
    """The user's installed Ponytail plugin for Claude Code (folder, version), if any."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        entry = json.loads((home / "plugins" / "installed_plugins.json").read_text())["plugins"][
            "ponytail@ponytail"
        ][0]
        folder = Path(entry["installPath"])
        manifest = json.loads((folder / ".claude-plugin" / "plugin.json").read_text())
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    return str(folder), str(manifest.get("version", ""))


def validate_claude_config(config) -> dict:
    if not isinstance(config, dict) or config.get("agent") != "claude_code":
        raise ValueError("Not a Claude Code chat configuration.")
    model = config.get("model") or "default"
    effort = config.get("effort") or "default"
    # An alias, or a full model id such as claude-opus-5-5.
    if not isinstance(model, str) or not (
        model in MODELS or (model.startswith("claude-") and model.replace("-", "").isalnum())
    ):
        raise ValueError("Choose a Claude model: default, sonnet, opus, haiku or a claude-… id.")
    if effort not in EFFORTS:
        raise ValueError("Effort is one of " + ", ".join(sorted(EFFORTS)) + ".")
    return {"version": 1, "agent": "claude_code", "model": model, "effort": effort}


def system_prompt(workflow_id: str, workspace_dir: str) -> str:
    """The same instructions and bundled skills Pi's extension gives its agent."""
    prompt = (HERE / "prompts" / "comfyui_agent.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{{WORKFLOW_ID}}", workflow_id).replace(
        "{{WORKSPACE_DIR}}", workspace_dir
    )
    skills = [
        (HERE / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for name in sorted(REQUIRED_SKILLS)
    ]
    return (
        prompt
        + "\n\nApply the following ComfyUI skills by default whenever relevant. Their full contents are "
        "already loaded here. They guide the studio's tools; they do not grant execution or installation "
        "authority.\n\n" + "\n\n".join(skills)
    )


def login_status() -> dict:
    """Whether Claude Code is installed and logged in (no account details)."""
    cli = claude_cli()
    if not cli:
        return {"installed": False, "logged_in": False}
    try:
        done = subprocess.run(
            [cli, "auth", "status"], capture_output=True, text=True, timeout=20, check=False
        )
        status = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"installed": True, "logged_in": False}
    return {
        "installed": True,
        "logged_in": status.get("loggedIn") is True,
        "method": status.get("authMethod"),
    }


def tool_name(name: str) -> str:
    return name.removeprefix(PREFIX)


class ClaudeChatSession(PiChatSession):
    def launch(self, config, origin):
        self.config = validate_claude_config(config)
        cli = claude_cli()
        if not cli:
            raise PiCLIError("Claude Code is not installed: the claude command was not found.")
        self.auth_source = "claude_login"
        self.local_dialogs: dict[str, asyncio.Future] = {}
        self.aborting = False
        self.text = ""
        self.tool_names: dict[str, str] = {}
        self.profile = tempfile.TemporaryDirectory(prefix="studio-claude-chat-")
        folder = Path(self.profile.name)
        (folder / "system_prompt.md").write_text(
            system_prompt(self.workspace.id, str(self.workspace.path)), encoding="utf-8"
        )
        # The tool server inherits this session's environment (the bridge token stays out of argv).
        server = {"command": sys.executable, "args": ["-I", str(HERE / "claude_mcp.py")]}
        (folder / "mcp.json").write_text(json.dumps({"mcpServers": {"studio": server}}))
        command = [
            cli,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--mcp-config",
            str(folder / "mcp.json"),
            "--strict-mcp-config",
            "--tools",
            "",  # no built-in tools: no shell, no host files, no web
            "--allowedTools",
            "mcp__studio",
            "--permission-mode",
            "dontAsk",  # anything not allowed above is refused, never prompted
            "--setting-sources",
            "",  # not the user's settings, hooks, plugins or CLAUDE.md
            "--system-prompt-file",
            str(folder / "system_prompt.md"),
            "--no-session-persistence",
        ]
        if self.config["model"] != "default":
            command += ["--model", self.config["model"]]
        if self.config["effort"] != "default":
            command += ["--effort", self.config["effort"]]
        environment = _pi_environment()
        environment.update(self.bridge_environment(origin))
        self.ponytail = ponytail_plugin()
        if self.ponytail:  # only this plugin: not the user's settings, other hooks or CLAUDE.md
            command += ["--plugin-dir", self.ponytail[0]]
            environment["PONYTAIL_DEFAULT_MODE"] = PONYTAIL_MODE  # not the user's saved default
        self.command, self.environment = command, environment
        self.model = {"provider": "claude-code", "id": self.config["model"]}

    def configuration(self):
        return dict(self.config)

    async def start(self):
        await asyncio.to_thread(self.workspace.perform, "workspace_info", {})
        self.transport = _PiRPCProcess(self.command, self.environment, cwd=self.profile.name)
        self.reader = asyncio.create_task(self.read_events())
        init = await asyncio.wait_for(self.control("initialize"), timeout=60)
        self.commands = [
            {"name": c["name"], "description": c.get("description", "")}
            for c in init.get("commands", [])
            if c.get("name") and not c["name"].startswith("__")  # internal
        ]
        status = await asyncio.wait_for(self.control("mcp_status"), timeout=60)
        studio = next((s for s in status.get("mcpServers", []) if s.get("name") == "studio"), {})
        have = {tool.get("name") for tool in studio.get("tools", [])}
        wanted = {t["name"] for t in json.loads((HERE / "agent_tools.json").read_text())}
        if studio.get("status") != "connected" or not wanted | {"read", "edit", "write"} <= have:
            raise PiCLIError("Claude Code could not load the studio's tools.")
        await self.socket.send_json(
            {
                "type": "ready",
                "agent": "claude_code",
                "agent_name": "Claude",
                "extensions": {"ponytail": {"version": self.ponytail[1], "mode": PONYTAIL_MODE}}
                if self.ponytail
                else {},
                "skills": {"names": sorted(REQUIRED_SKILLS), "default_loaded": True},
                "schema_only_prompt": False,
                "workspace_files_ui": True,
                "workspace": {
                    "workflow_id": self.workspace.id,
                    "graph_id": self.graph_id,
                    "root": str(self.workspace.path),
                    "file_tools_scoped": True,
                    "shell_enabled": False,
                },
                "model": self.model,
                "auth_source": self.auth_source,
                "pi_config": self.configuration(),
                "mcp_tool_count": len(have),
                "commands": self.commands,
            }
        )
        self.initialized.set()

    async def control(self, subtype, **fields):
        """A control request to Claude Code (initialize, mcp_status, set_model, interrupt)."""
        request_id = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        self.rpc_pending[request_id] = future
        try:
            await self.send(
                {
                    "type": "control_request",
                    "request_id": request_id,
                    "request": {"subtype": subtype, **fields},
                }
            )
            response = await asyncio.wait_for(future, timeout=60)
            if response.get("subtype") != "success":
                raise PiCLIError(str(response.get("error") or f"Claude Code refused {subtype}."))
            return response.get("response") or {}
        finally:
            self.rpc_pending.pop(request_id, None)

    async def client_message(self, record):
        kind = record.get("type")
        if kind == "prompt":
            if not self.initialized.is_set():
                raise ValueError("Wait for Claude Code to finish connecting first.")
            text = record.get("message")
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ValueError("Send 1–16,000 characters.")
            if self.busy:
                raise ValueError("Stop or wait for the current reply first.")
            model = text.strip().removeprefix("/model ").strip()
            if text.startswith("/model ") and model:  # as the picker does, so its label stays true
                await self.client_message(
                    {"type": "configure", "pi_config": {**self.config, "model": model}}
                )
                await self.reply_locally(f"Model: {model}")
                return
            self.busy, self.turns, self.aborting = True, 0, False
            await self.socket.send_json({"type": "agent_start"})
            await self.send({"type": "user", "message": {"role": "user", "content": text}})
        elif kind == "configure":
            if not self.initialized.is_set() or self.busy:
                raise ValueError("Wait for Claude to finish before changing models.")
            config = validate_claude_config(record.get("pi_config"))
            if config["effort"] != self.config["effort"]:
                raise ValueError("Start New chat to change the effort level.")
            await self.control(
                "set_model", model=None if config["model"] == "default" else config["model"]
            )
            self.config, self.model["id"] = config, config["model"]
            await self.socket.send_json({"type": "model_config", "pi_config": self.configuration()})
        elif kind == "abort":
            self.aborting = True
            self.cancel_pending()
            if self.busy:
                await self.control("interrupt")
        else:
            await super().client_message(record)

    async def answer_dialog(self, response):
        future = self.local_dialogs.pop(response["id"], None)
        if future and not future.done():
            future.set_result(response)

    def cancel_pending(self):
        super().cancel_pending()
        for future in self.local_dialogs.values():
            if not future.done():
                future.cancel()
        self.local_dialogs.clear()

    async def dialog(self, method, **fields):
        """Ask the user in the chat panel (the panel's own permission card) and wait for the answer."""
        dialog_id = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        self.dialogs[dialog_id], self.local_dialogs[dialog_id] = method, future
        await self.socket.send_json(
            {"type": "extension_ui_request", "id": dialog_id, "method": method, **fields}
        )
        return await future

    async def agent_action(self, action, arguments):
        if action == "confirm":
            answer = await self.dialog(
                "confirm",
                title=str(arguments.get("title", "Allow?"))[:200],
                message=str(arguments.get("message", ""))[:4000],
            )
            return {"confirmed": answer.get("confirmed") is True}
        if action == "ask_user":
            return await self.ask_user(arguments)
        return await super().agent_action(action, arguments)

    async def ask_user(self, arguments):
        """comfy_ask_user: each question with its options plus a free-text Other, as in Pi."""
        other = "Other (I'll type my answer)"
        answers = []
        for item in (arguments or {}).get("questions", [])[:4]:
            question = str(item.get("question", ""))[:300]
            options = [str(o)[:200] for o in item.get("options", [])[:4]]
            answer = await self.dialog("select", title=question, options=[*options, other])
            value = answer.get("value")
            if value == other:
                value = (
                    await self.dialog("input", title=question, placeholder="Type your answer")
                ).get("value")
            if answer.get("cancelled") or value is None:
                return {
                    "answers": answers,
                    "dismissed": True,
                    "message": "The user dismissed the questions. Do not assume answers; ask in chat what they want.",
                }
            answers.append({"question": question, "answer": value})
        return {"answers": answers}

    async def read_events(self):
        try:
            while not self.socket.closed:
                try:
                    event = await asyncio.to_thread(
                        self.transport.next_record, time.monotonic() + 1
                    )
                except TimeoutError:
                    continue
                for visible in self.translate(event):
                    await self.socket.send_json(visible)
                if (
                    event.get("type") == "stream_event"
                    and event["event"].get("type") == "message_start"
                ):
                    self.turns += 1
                    if self.turns > MAX_TURNS and not self.aborting:
                        self.aborting = True
                        await self.control("interrupt")
                        await self.socket.send_json(
                            {
                                "type": "chat_error",
                                "error": f"Stopped after {MAX_TURNS} agent turns. Say continue to go on.",
                            }
                        )
        except (PiCLIError, ConnectionError, RuntimeError) as error:
            logging.warning("Claude Code chat ended: %s", error)
            if not self.socket.closed:
                await self.socket.send_json({"type": "chat_error", "error": str(error)[:2000]})
                await self.socket.close()

    def translate(self, event) -> list[dict]:
        """Claude Code stream events → the chat panel's events. Thinking is never passed on."""
        kind = event.get("type")
        if kind == "control_response":
            response = event.get("response", {})
            future = self.rpc_pending.get(response.get("request_id"))
            if future and not future.done():
                future.set_result(response)
            return []
        if kind == "stream_event":
            inner = event.get("event", {})
            if inner.get("type") == "message_start":
                self.text = ""
                return [{"type": "message_start", "message": {"role": "assistant"}}]
            delta = inner.get("delta", {})
            if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self.text += delta["text"]
                return [
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {"type": "text_delta", "delta": delta["text"]},
                    }
                ]
            if inner.get("type") == "message_stop":
                stop = "aborted" if self.aborting else "stop"
                message = {
                    "role": "assistant",
                    "content": [{"type": "text", "text": self.text}],
                    "stopReason": stop,
                }
                return [{"type": "message_end", "message": message}]
            return []
        if kind == "assistant":
            started = []
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = tool_name(block.get("name", ""))
                    self.tool_names[block.get("id")] = name
                    started.append(
                        {
                            "type": "tool_execution_start",
                            "toolCallId": block.get("id"),
                            "toolName": name,
                            "args": block.get("input", {}),
                        }
                    )
            return started
        if kind == "user":
            ended = []
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    result = block.get("content")
                    if isinstance(result, str):
                        result = [{"type": "text", "text": result}]
                    ended.append(
                        {
                            "type": "tool_execution_end",
                            "toolCallId": block.get("tool_use_id"),
                            "toolName": self.tool_names.get(block.get("tool_use_id"), "tool"),
                            "result": {
                                "content": [b for b in result or [] if b.get("type") == "text"]
                            },
                            "isError": block.get("is_error") is True,
                        }
                    )
            return ended
        if kind == "result":
            self.busy = False
            self.cancel_pending()
            out = []
            if event.get("is_error") and not self.aborting:
                out.append(
                    {
                        "type": "chat_error",
                        "error": str(
                            event.get("result") or event.get("subtype") or "Claude Code failed."
                        )[:2000],
                    }
                )
            elif self.turns == 0 and event.get("result"):
                # A local command's output (/context, /usage): no model turn streamed it.
                text = [{"type": "text", "text": str(event["result"])}]
                out += [
                    {"type": "message_start", "message": {"role": "assistant"}},
                    {"type": "message_end", "message": {"role": "assistant", "content": text}},
                ]
            return [*out, {"type": "agent_settled"}]
        return []
