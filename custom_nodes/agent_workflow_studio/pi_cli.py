"""Schema-constrained Pi CLI backend using its JSONL RPC protocol."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .pi_auth import (
    all_provider_api_key_env_names,
    provider_credential_environment,
)
from .pi_model_config import validate_pi_model_config
from .structured_json import build_validator, parse_validated_object

PI_CLI_ENV = "AGENT_WORKFLOW_STUDIO_PI_CLI"
PI_TOTAL_TIMEOUT_SECONDS = 600
STRUCTURED_OUTPUT_TOOL_NAME = "agent_workflow_submit_json"
STRUCTURED_OUTPUT_SCHEMA_ENV = "PI_STRUCTURED_OUTPUT_SCHEMA"
VALID_THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "max"})
PI_SYSTEM_PROMPT = (
    "You are a bounded structured-output assistant. Use the single schema-bound "
    "typed-output tool exactly once to return the complete result. Do not emit a "
    "separate text response or use tools for actions, code execution, or resource access."
)


class PiCLIError(RuntimeError):
    pass


class _PiRPCProcess:
    def __init__(self, command: list[str], environment: dict[str, str] | None = None) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(Path.home()),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment if environment is not None else _pi_environment(),
                shell=False,
            )
        except OSError as exc:
            raise PiCLIError(f"Could not start Pi CLI ({type(exc).__name__})") from None
        self.records: queue.Queue[bytes | None] = queue.Queue()
        self.pending: deque[dict[str, Any]] = deque()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()
        self.return_code: int | None = None

    def send(self, record: dict[str, Any]) -> None:
        stream = self.process.stdin
        if stream is None or self.process.poll() is not None:
            raise PiCLIError("Pi CLI exited before accepting the request")
        try:
            stream.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
            stream.flush()
        except OSError:
            raise PiCLIError("Could not send a request to Pi CLI") from None

    def next_record(self, deadline: float) -> dict[str, Any]:
        if self.pending:
            return self.pending.popleft()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Pi CLI run exceeded its time budget")
            try:
                line = self.records.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise PiCLIError(
                        "Pi CLI exited before completing the run "
                        f"(status {self.process.returncode})"
                    )
                continue
            if line is None:
                raise PiCLIError("Pi CLI closed its event stream before completing the run")
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise PiCLIError("Pi CLI emitted an invalid RPC record") from None
            if not isinstance(record, dict):
                raise PiCLIError("Pi CLI emitted an invalid RPC record")
            return record

    def command(
        self, command_type: str, payload: dict[str, Any], deadline: float
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        self.send({"id": request_id, "type": command_type, **payload})
        deferred = []
        try:
            while True:
                record = self.next_record(deadline)
                if _is_response_for(record, request_id, command_type):
                    if not record.get("success"):
                        raise PiCLIError(f"Pi CLI rejected the {command_type} command")
                    data = record.get("data")
                    return data if isinstance(data, dict) else {}
                deferred.append(record)
        finally:
            for record in reversed(deferred):
                self.pending.appendleft(record)

    def abort(self) -> int | None:
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
        return self.close()

    def close(self) -> int | None:
        stream = self.process.stdin
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass
        try:
            self.return_code = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.return_code = self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.return_code = self.process.wait()
        if self.process.stdout is not None:
            self.process.stdout.close()
        self.reader.join(timeout=1)
        return self.return_code

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self.records.put(None)
            return
        try:
            for line in iter(stream.readline, b""):
                self.records.put(line)
        finally:
            self.records.put(None)


def complete_json(
    task: str,
    schema_text: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    system_prompt: str = "",
    on_progress: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    reasoning_timeout_seconds: float = 120,
    pi_config: dict[str, Any] | None = None,
    total_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if not task.strip() or len(task) > 32_000:
        raise ValueError("task must contain 1–32,000 characters")
    if not 1 <= max_tokens <= 32768:
        raise ValueError("max_tokens is out of range")
    if total_timeout_seconds is not None and (
        type(total_timeout_seconds) is not int
        or not 1 <= total_timeout_seconds <= PI_TOTAL_TIMEOUT_SECONDS
    ):
        raise ValueError("total_timeout_seconds must be within the Pi total deadline")
    if reasoning_timeout_seconds <= 0:
        raise ValueError("reasoning_timeout_seconds must be positive")
    validator = build_validator(schema_text)
    config = validate_pi_model_config(pi_config) if pi_config is not None else None
    command, provider, model, thinking_level = _build_command(config, structured_output=True)
    prompt = _build_prompt(task, system_prompt, max_tokens)
    environment = _pi_environment()
    environment[STRUCTURED_OUTPUT_SCHEMA_ENV] = schema_text
    temporary_agent_dir = None
    credential_field_names: tuple[str, ...] = ()
    auth_source = config["credential_source"] if config else "pi_managed"
    if auth_source == "studio_keychain":
        if not provider:
            raise PiCLIError("Studio Keychain auth requires an explicit Pi provider")
        credential_environment = provider_credential_environment(provider)
        for env_name in all_provider_api_key_env_names():
            environment.pop(env_name, None)
        if not credential_environment:
            raise PiCLIError(f"No Studio Keychain credential is saved for Pi provider {provider}")
        temporary_agent_dir = tempfile.TemporaryDirectory(prefix="agent-workflow-studio-pi-")
        environment["PI_CODING_AGENT_DIR"] = temporary_agent_dir.name
        environment.update(credential_environment)
        credential_field_names = tuple(sorted(credential_environment))
    _notify_event(
        on_event,
        {
            "type": "auth_source",
            "auth_source": auth_source,
            "credential_fields": credential_field_names,
        },
    )
    started_at = time.monotonic()
    answer_deadline = started_at + reasoning_timeout_seconds
    total_deadline = started_at + (
        total_timeout_seconds
        if total_timeout_seconds is not None
        else max(PI_TOTAL_TIMEOUT_SECONDS, reasoning_timeout_seconds)
    )
    transport: _PiRPCProcess | None = None
    state: dict[str, Any] = {
        "answer_parts": [],
        "final_text": None,
        "answer_chars": 0,
        "reasoning_events": 0,
        "reasoning_chars": 0,
        "finish_reason": None,
        "usage": {},
        "last_progress_at": 0.0,
        "last_progress_chars": 0,
        "prompt_accepted": False,
        "connected": False,
        "tool_attempt": False,
        "output_tool_call_count": 0,
        "structured_output": None,
        "validated_output": None,
        "started_at": started_at,
    }
    try:
        transport = _PiRPCProcess(command, environment)
        model_state = transport.command("get_state", {}, total_deadline)
        retry_state = transport.command("set_auto_retry", {"enabled": False}, total_deadline)
        if retry_state.get("enabled") is True:
            raise PiCLIError("Pi CLI automatic retries could not be disabled")
        active_provider, active_model, active_thinking = _model_state(
            model_state, provider, model, thinking_level
        )
        _notify_event(
            on_event,
            {
                "type": "configured",
                "provider": active_provider,
                "model": active_model,
                "thinking_level": active_thinking,
                "enabled_tool_names": [STRUCTURED_OUTPUT_TOOL_NAME],
                "external_tools_enabled": False,
                "session_persisted": False,
                "automatic_retries": False,
                "auth_source": auth_source,
            },
        )
        prompt_id = str(uuid.uuid4())
        transport.send({"id": prompt_id, "type": "prompt", "message": prompt})
        while True:
            deadline = total_deadline
            if not state["answer_parts"] and not state["final_text"]:
                deadline = min(deadline, answer_deadline)
            record = _next_record(transport, deadline, state, on_event)
            record_type = record.get("type")
            if _is_response_for(record, prompt_id, "prompt"):
                if not record.get("success"):
                    raise PiCLIError("Pi CLI did not accept the prompt")
                prompt_data = record.get("data")
                disposition = (
                    prompt_data.get("disposition") if isinstance(prompt_data, dict) else None
                )
                if disposition == "handled":
                    raise PiCLIError("Pi CLI handled the prompt without starting a run")
                state["prompt_accepted"] = True
                continue
            if record_type in {
                "tool_execution_start",
                "tool_execution_update",
                "tool_execution_end",
            }:
                raise PiCLIError("Pi CLI started tool execution before output capture")
            if record_type == "auto_retry_start" or (
                record_type == "agent_end" and record.get("willRetry") is True
            ):
                raise PiCLIError("Pi CLI attempted an automatic retry")
            if record_type == "message_start":
                message = record.get("message", {})
                if isinstance(message, dict) and message.get("role") == "assistant":
                    state["connected"] = True
                    _notify_event(
                        on_event,
                        {
                            "type": "connected",
                            "provider": active_provider,
                            "model": active_model,
                            "thinking_level": active_thinking,
                        },
                    )
            elif record_type == "message_update":
                _handle_message_update(record, state, on_progress, on_event, validator)
            elif record_type == "message_end":
                _handle_message_end(record, state, on_progress, on_event)
                if state["structured_output"] is not None:
                    if state["finish_reason"] != "toolUse":
                        raise PiCLIError("Pi CLI typed output was not a tool-call result")
                    result = state["validated_output"]
                    transport.abort()
                    _notify_event(on_event, {"type": "structured_output_validated"})
                    return result
            elif record_type == "agent_settled":
                break
        if not state["prompt_accepted"]:
            raise PiCLIError("Pi CLI settled without accepting the prompt")
        raise PiCLIError("Pi CLI completed without the typed output tool")
    except Exception:
        if transport is not None and transport.return_code is None:
            transport.abort()
        raise
    finally:
        if transport is not None and transport.return_code is None:
            transport.close()
        if temporary_agent_dir is not None:
            temporary_agent_dir.cleanup()


def _is_response_for(record: dict[str, Any], request_id: str, command_type: str) -> bool:
    return record.get("type") == "response" and (
        record.get("id") == request_id
        or (record.get("id") is None and record.get("command") == command_type)
    )


def _pi_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith(("NAYOSE_", "COMFYUI_", "AGENT_WORKFLOW_STUDIO_")):
            environment.pop(key)
    environment.pop("PYTHONPATH", None)
    environment.pop(STRUCTURED_OUTPUT_SCHEMA_ENV, None)
    return environment


def _build_command(
    config: dict[str, Any] | None = None,
    *,
    offline: bool = False,
    include_environment: bool = True,
    structured_output: bool = False,
) -> tuple[list[str], str | None, str | None, str | None]:
    executable = os.environ.get(PI_CLI_ENV) or shutil.which("pi")
    if not executable:
        raise PiCLIError("Pi CLI was not found; install it or set AGENT_WORKFLOW_STUDIO_PI_CLI")
    provider = (
        config["provider"]
        if config
        else os.environ.get("PI_PROVIDER")
        if include_environment
        else None
    )
    model = (
        config["model"] if config else os.environ.get("PI_MODEL") if include_environment else None
    )
    if bool(provider) != bool(model):
        raise PiCLIError("Set both PI_PROVIDER and PI_MODEL, or neither")
    thinking_level = (
        config["thinking_level"]
        if config
        else os.environ.get("PI_REASONING_LEVEL")
        if include_environment
        else None
    )
    if thinking_level and thinking_level not in VALID_THINKING_LEVELS:
        raise PiCLIError("PI_REASONING_LEVEL is not a supported Pi thinking level")
    command = [
        executable,
        "--mode",
        "rpc",
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-context-files",
        "--no-prompt-templates",
        "--no-themes",
        "--no-approve",
        "--system-prompt",
        PI_SYSTEM_PROMPT,
    ]
    if structured_output:
        extension_path = Path(__file__).with_name("pi_structured_output_extension.ts")
        command.extend(
            (
                "--no-builtin-tools",
                "--extension",
                str(extension_path),
                "--tools",
                STRUCTURED_OUTPUT_TOOL_NAME,
            )
        )
    else:
        command.append("--no-tools")
    if provider and model:
        command.extend(("--provider", provider, "--model", model))
    if thinking_level:
        command.extend(("--thinking", thinking_level))
    if offline:
        command.append("--offline")
    return command, provider, model, thinking_level


def list_available_models() -> list[dict[str, Any]]:
    command, _, _, _ = _build_command(offline=True, include_environment=False)
    environment = _pi_environment()
    environment["PI_OFFLINE"] = "1"
    transport = _PiRPCProcess(command, environment)
    deadline = time.monotonic() + 30
    try:
        data = transport.command("get_available_models", {}, deadline)
        models = data.get("models", [])
        if not isinstance(models, list):
            return []
        return [
            metadata
            for model in models
            if isinstance(model, dict)
            if (metadata := _public_model_metadata(model))
        ]
    finally:
        if transport.return_code is None:
            transport.close()


def check_auth(provider: str, model: str, credential_source: str) -> dict[str, Any]:
    config = validate_pi_model_config(
        {
            "version": 1,
            "provider": provider,
            "model": model,
            "thinking_level": "medium",
            "credential_source": credential_source,
        }
    )
    executable = os.environ.get(PI_CLI_ENV) or shutil.which("pi")
    if not executable:
        raise PiCLIError("Pi CLI was not found")
    environment = _pi_environment()
    temporary_agent_dir = None
    if credential_source == "studio_keychain":
        credentials = provider_credential_environment(provider)
        for env_name in all_provider_api_key_env_names():
            environment.pop(env_name, None)
        if not credentials:
            return {
                "status": "not_ready",
                "auth_source": credential_source,
                "network_requested": False,
            }
        temporary_agent_dir = tempfile.TemporaryDirectory(prefix="agent-workflow-studio-pi-auth-")
        environment["PI_CODING_AGENT_DIR"] = temporary_agent_dir.name
        environment.update(credentials)
    command = [
        executable,
        "auth",
        "check",
        "--provider",
        config["provider"],
        "--model",
        config["model"],
        "--json",
        "--no-refresh",
    ]
    try:
        try:
            result = subprocess.run(
                command,
                cwd=str(Path.home()),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=15,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PiCLIError("Pi authentication status check failed") from None
        auth_details = _auth_details(result.stdout, result.returncode)
        return {
            **auth_details,
            "auth_source": credential_source,
            "provider": config["provider"],
            "model": config["model"],
            "network_requested": False,
        }
    finally:
        if temporary_agent_dir is not None:
            temporary_agent_dir.cleanup()


def _public_model_metadata(model: dict[str, Any]) -> dict[str, Any]:
    provider = model.get("provider")
    model_id = model.get("id")
    if not isinstance(provider, str) or not isinstance(model_id, str):
        return {}
    metadata: dict[str, Any] = {
        "provider": provider,
        "id": model_id,
        "name": model.get("name") if isinstance(model.get("name"), str) else model_id,
    }
    for key in ("api", "reasoning", "input", "contextWindow", "maxTokens"):
        value = model.get(key)
        if isinstance(value, (str, bool, int, list)) and not isinstance(value, dict):
            metadata[key] = value
    return metadata


def _auth_details(output: str, return_code: int) -> dict[str, str]:
    fallback = {0: "ready", 1: "not_ready", 2: "invalid"}.get(return_code, "error")
    details = {"status": fallback}
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return details
    if not isinstance(result, dict):
        return details
    status = result.get("status")
    if isinstance(status, str) and status in {"ready", "not_ready", "invalid"}:
        details["status"] = status
    auth_type = result.get("authType")
    if isinstance(auth_type, str) and re.fullmatch(r"[a-z0-9_-]{1,32}", auth_type):
        details["auth_method"] = auth_type
    reason = result.get("reason")
    if isinstance(reason, str) and reason in {
        "provider_not_found",
        "credentials_not_configured",
        "credential_not_available",
        "invalid_state",
    }:
        details["reason"] = reason
    return details


def _build_prompt(task: str, system_prompt: str, max_tokens: int) -> str:
    sections = [
        (
            f"Use {STRUCTURED_OUTPUT_TOOL_NAME} exactly once to return the complete "
            "result through its schema-defined parameters. Do not emit a separate "
            "assistant text response."
        ),
        f"Output size target: at most {max_tokens} tokens.",
    ]
    if system_prompt.strip():
        sections.extend(("Role instructions:", system_prompt.strip()))
    sections.extend(("Task input:", task))
    return "\n\n".join(sections)


def _model_state(
    state: dict[str, Any],
    requested_provider: str | None,
    requested_model: str | None,
    requested_thinking: str | None,
) -> tuple[str | None, str | None, str | None]:
    model_state = state.get("model")
    provider = requested_provider
    model = requested_model
    if isinstance(model_state, dict):
        provider = model_state.get("provider") or provider
        model = model_state.get("id") or model
    thinking = state.get("thinkingLevel") or requested_thinking
    if not isinstance(provider, str) or not isinstance(model, str):
        raise PiCLIError(
            "Pi CLI has no selected provider/model; configure PI_PROVIDER and PI_MODEL"
        )
    return provider, model, thinking if isinstance(thinking, str) else None


def _next_record(
    transport: _PiRPCProcess,
    deadline: float,
    state: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    try:
        return transport.next_record(deadline)
    except TimeoutError:
        if not state["answer_parts"] and not state["final_text"]:
            _notify_event(
                on_event,
                {
                    "type": "reasoning_budget_exceeded",
                    "elapsed_seconds": round(time.monotonic() - state["started_at"], 1),
                    "reasoning_events": state["reasoning_events"],
                    "reasoning_chars": state["reasoning_chars"],
                },
            )
            raise PiReasoningBudgetExceeded(
                "Pi CLI produced no typed output before its reasoning deadline; "
                f"reasoning_events={state['reasoning_events']}; "
                f"reasoning_chars={state['reasoning_chars']}"
            ) from None
        raise PiCLIError("Pi CLI exceeded its total run time") from None


class PiReasoningBudgetExceeded(RuntimeError):
    pass


def _handle_message_update(
    record: dict[str, Any],
    state: dict[str, Any],
    on_progress: Callable[[str], None] | None,
    on_event: Callable[[dict[str, Any]], None] | None,
    validator: Any,
) -> None:
    usage = _pi_usage(record.get("usage"))
    if usage:
        state["usage"] = usage
        _notify_event(on_event, {"type": "usage", "usage": usage})
    event = record.get("assistantMessageEvent")
    if not isinstance(event, dict):
        return
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type.startswith("toolcall"):
        if event_type == "toolcall_start":
            if event.get("toolName") != STRUCTURED_OUTPUT_TOOL_NAME:
                raise PiCLIError("Pi CLI attempted a disallowed tool call")
            state["output_tool_call_count"] += 1
            if state["output_tool_call_count"] != 1:
                raise PiCLIError("Pi CLI attempted multiple typed output calls")
            state["tool_attempt"] = True
            return
        if event_type == "toolcall_delta":
            return
        if event_type == "toolcall_end":
            _capture_structured_tool_call(event.get("toolCall"), state, on_event, validator)
            return
        raise PiCLIError("Pi CLI emitted an unexpected tool-call event")
    if event_type == "thinking_delta":
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            state["reasoning_events"] += 1
            state["reasoning_chars"] += len(delta)
            _notify_event(
                on_event,
                {"type": "reasoning_delta", "characters": len(delta)},
            )
        return
    if event_type != "text_delta":
        return
    delta = event.get("delta")
    if not isinstance(delta, str) or not delta:
        return
    state["answer_parts"].append(delta)
    state["answer_chars"] += len(delta)
    _notify_event(on_event, {"type": "answer_delta", "characters": len(delta)})
    now = time.monotonic()
    if on_progress is not None and (
        now - state["last_progress_at"] >= 0.2
        or state["answer_chars"] - state["last_progress_chars"] >= 160
    ):
        state["last_progress_at"] = now
        state["last_progress_chars"] = state["answer_chars"]
        _notify_progress(on_progress, "".join(state["answer_parts"])[-1500:])


def _capture_structured_tool_call(
    tool_call: Any,
    state: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None,
    validator: Any,
) -> None:
    if not isinstance(tool_call, dict):
        raise PiCLIError("Pi CLI emitted an invalid typed output call")
    if tool_call.get("name") != STRUCTURED_OUTPUT_TOOL_NAME:
        raise PiCLIError("Pi CLI attempted a disallowed tool call")
    arguments = tool_call.get("arguments")
    call_id = tool_call.get("id")
    if not isinstance(arguments, dict) or not isinstance(call_id, str):
        raise PiCLIError("Pi CLI emitted invalid typed output arguments")
    if state["structured_output"] is not None:
        raise PiCLIError("Pi CLI attempted multiple typed output calls")
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise PiCLIError("Pi CLI typed output arguments were not JSON serializable") from None
    validated = parse_validated_object(encoded, validator, "Pi CLI")
    state["structured_output"] = arguments
    state["validated_output"] = validated
    _notify_event(on_event, {"type": "structured_output_received"})


def _handle_message_end(
    record: dict[str, Any],
    state: dict[str, Any],
    on_progress: Callable[[str], None] | None,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> None:
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return
    content = message.get("content", [])
    text = (
        "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if isinstance(content, list)
        else ""
    )
    if text:
        state["final_text"] = text
        if on_progress is not None:
            _notify_progress(on_progress, text[-1500:])
    state["finish_reason"] = message.get("stopReason")
    if isinstance(state["finish_reason"], str):
        _notify_event(
            on_event,
            {"type": "finish", "finish_reason": state["finish_reason"]},
        )
    usage = _pi_usage(message.get("usage"))
    if usage:
        state["usage"] = usage
        _notify_event(on_event, {"type": "usage", "usage": usage})
    if state["finish_reason"] == "error":
        raise PiCLIError("Pi CLI provider request failed")
    if state["finish_reason"] == "toolUse" and not state["tool_attempt"]:
        raise PiCLIError("Pi CLI used an unexpected tool")


def _pi_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    mapping = {
        "input": "input_tokens",
        "output": "output_tokens",
        "reasoning": "reasoning_tokens",
        "totalTokens": "total_tokens",
    }
    return {
        target: count
        for source, target in mapping.items()
        if (count := value.get(source)) is not None
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    }


def _notify_event(callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    with suppress(Exception):
        callback(event)


def _notify_progress(callback: Callable[[str], None], text: str) -> None:
    with suppress(Exception):
        callback(text)


__all__ = ["PI_CLI_ENV", "PI_TOTAL_TIMEOUT_SECONDS", "PiCLIError", "complete_json"]
