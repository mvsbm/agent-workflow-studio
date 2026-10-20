import json
import queue
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from custom_nodes.agent_workflow_studio import llm_harness, pi_cli
from custom_nodes.agent_workflow_studio.llm_harness import LLMRunHarness

SCHEMA = '{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}'
PRIVATE_THOUGHT = "private reasoning must never be surfaced"


class FakeStdout:
    def __init__(self):
        self.records = queue.Queue()

    def readline(self):
        record = self.records.get()
        return b"" if record is None else record

    def close(self):
        pass


class FakeStdin:
    def __init__(self, process, response_mode):
        self.process = process
        self.response_mode = response_mode
        self.closed = False

    def write(self, data):
        command = json.loads(data)
        self.process.commands.append(command)
        if command["type"] == "get_state":
            self.process.emit({"type": "session_start"})
            self.process.emit(
                {
                    "type": "response",
                    "id": command["id"],
                    "success": True,
                    "data": {
                        "model": {"provider": "test-provider", "id": "test-model"},
                        "thinkingLevel": "high",
                    },
                }
            )
        elif command["type"] == "set_auto_retry":
            self.process.emit({"type": "response", "id": command["id"], "success": True})
        elif command["type"] == "prompt":
            self.process.emit(
                {
                    "type": "response",
                    "id": command["id"],
                    "success": True,
                    "data": {"disposition": "started"},
                }
            )
            self.process.emit({"type": "message_start", "message": {"role": "assistant"}})
            if self.response_mode == "answer":
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "thinking_delta",
                            "delta": PRIVATE_THOUGHT,
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "text_delta",
                            "delta": '{"answer":"ok"}',
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": PRIVATE_THOUGHT},
                                {"type": "text", "text": '{"answer":"ok"}'},
                            ],
                            "stopReason": "stop",
                            "usage": {
                                "input": 21,
                                "output": 7,
                                "reasoning": 3,
                                "totalTokens": 31,
                            },
                        },
                    }
                )
                self.process.emit({"type": "agent_settled"})
            elif self.response_mode == "structured_output":
                arguments = {"answer": "PRIVATE_STRUCTURED_ARG"}
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "thinking_delta",
                            "delta": PRIVATE_THOUGHT,
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "toolcall_start",
                            "contentIndex": 0,
                            "id": "call-1",
                            "toolName": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "toolcall_end",
                            "contentIndex": 0,
                            "toolCall": {
                                "type": "toolCall",
                                "id": "call-1",
                                "name": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                                "arguments": arguments,
                            },
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call-1",
                                    "name": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                                    "arguments": arguments,
                                }
                            ],
                            "stopReason": "toolUse",
                            "usage": {
                                "input": 21,
                                "output": 7,
                                "reasoning": 3,
                                "totalTokens": 31,
                            },
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": "call-1",
                        "toolName": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": arguments,
                    }
                )
                self.process.emit(
                    {
                        "type": "tool_execution_end",
                        "toolCallId": "call-1",
                        "toolName": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                        "result": {
                            "content": [{"type": "text", "text": "Structured result accepted."}],
                            "details": None,
                        },
                        "isError": False,
                    }
                )
                self.process.emit({"type": "agent_settled"})
            elif self.response_mode == "invalid_structured_output":
                arguments = {"wrong": "PRIVATE_INVALID_OUTPUT"}
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "toolcall_start",
                            "contentIndex": 0,
                            "id": "call-1",
                            "toolName": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "toolcall_end",
                            "contentIndex": 0,
                            "toolCall": {
                                "type": "toolCall",
                                "id": "call-1",
                                "name": pi_cli.STRUCTURED_OUTPUT_TOOL_NAME,
                                "arguments": arguments,
                            },
                        },
                    }
                )
            elif self.response_mode == "invalid_json":
                response = "PRIVATE_RESPONSE_TEXT that is not JSON"
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "text_delta",
                            "delta": response,
                        },
                    }
                )
                self.process.emit(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": response}],
                            "stopReason": "stop",
                            "usage": {"input": 20, "output": 8, "total": 28},
                        },
                    }
                )
                self.process.emit({"type": "agent_settled"})
            elif self.response_mode == "tool":
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "toolcall_start",
                            "toolName": "bash",
                            "arguments": "must not be exposed",
                        },
                    }
                )
            elif self.response_mode == "reasoning_only":
                self.process.emit(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {
                            "type": "thinking_delta",
                            "delta": PRIVATE_THOUGHT,
                        },
                    }
                )
        return len(data)

    def flush(self):
        pass

    def close(self):
        if not self.closed:
            self.closed = True
            self.process.returncode = 0
            self.process.stdout.records.put(None)


class FakeProcess:
    def __init__(self, response_mode):
        self.commands = []
        self.stdout = FakeStdout()
        self.returncode = None
        self.stdin = FakeStdin(self, response_mode)

    def emit(self, record):
        self.stdout.records.put(json.dumps(record).encode() + b"\n")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self.stdout.records.put(None)

    def kill(self):
        self.returncode = -9
        self.stdout.records.put(None)


class PiCliTests(unittest.TestCase):
    def test_explicit_total_deadline_bounds_transport_commands(self):
        process = FakeProcess("structured_output")
        deadlines = []
        original = pi_cli._PiRPCProcess.command

        def observed(transport, kind, data, deadline):
            deadlines.append(deadline - pi_cli.time.monotonic())
            return original(transport, kind, data, deadline)

        runtime = self._patch_runtime(process)
        with (
            runtime[0],
            runtime[1],
            runtime[2],
            patch.object(pi_cli._PiRPCProcess, "command", observed),
        ):
            pi_cli.complete_json("Test task", SCHEMA, total_timeout_seconds=120)
        self.assertTrue(deadlines)
        self.assertTrue(all(0 < value <= 120 for value in deadlines))
        for value in [0, 601, True]:
            with self.assertRaises(ValueError):
                pi_cli.complete_json("Test task", SCHEMA, total_timeout_seconds=value)

    def test_pi_rpc_uses_typed_output_tool_and_keeps_arguments_private(self):
        process = FakeProcess("structured_output")
        events = []
        progress = []
        runtime_patches = self._patch_runtime(process)
        with runtime_patches[0], runtime_patches[1], runtime_patches[2] as popen:
            result = pi_cli.complete_json(
                "Test task",
                SCHEMA,
                on_event=events.append,
                on_progress=progress.append,
            )

        self.assertEqual(result, {"answer": "PRIVATE_STRUCTURED_ARG"})
        self.assertNotIn("PRIVATE_STRUCTURED_ARG", str(events))
        self.assertNotIn("PRIVATE_STRUCTURED_ARG", " ".join(progress))
        command = popen.call_args.args[0]
        self.assertIn("--no-builtin-tools", command)
        self.assertIn("--no-extensions", command)
        self.assertIn("--extension", command)
        self.assertIn("--tools", command)
        self.assertIn(pi_cli.STRUCTURED_OUTPUT_TOOL_NAME, command)
        self.assertNotIn("--no-tools", command)
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment[pi_cli.STRUCTURED_OUTPUT_SCHEMA_ENV], SCHEMA)
        self.assertNotIn("Test task", command)

    def _patch_runtime(self, process):
        return (
            patch.object(pi_cli.shutil, "which", return_value="/opt/homebrew/bin/pi"),
            patch.dict(
                "os.environ",
                {
                    "PI_PROVIDER": "test-provider",
                    "PI_MODEL": "test-model",
                    "PI_REASONING_LEVEL": "high",
                    "NAYOSE_API_TOKEN": "test-only-nayose-token",
                },
                clear=False,
            ),
            patch.object(pi_cli.subprocess, "Popen", return_value=process),
        )

    def test_pi_rpc_stream_validates_typed_output_and_reports_safe_metadata(self):
        process = FakeProcess("structured_output")
        events = []
        progress = []
        runtime_patches = self._patch_runtime(process)
        with runtime_patches[0], runtime_patches[1], runtime_patches[2] as popen:
            result = pi_cli.complete_json(
                "Test task",
                SCHEMA,
                on_event=events.append,
                on_progress=progress.append,
            )

        self.assertEqual(result, {"answer": "PRIVATE_STRUCTURED_ARG"})
        self.assertTrue(any(event["type"] == "configured" for event in events))
        self.assertTrue(any(event["type"] == "connected" for event in events))
        self.assertTrue(any(event["type"] == "reasoning_delta" for event in events))
        self.assertTrue(
            {event["type"] for event in events}
            >= {"finish", "usage", "structured_output_validated"}
        )
        usage = next(event["usage"] for event in events if event["type"] == "usage")
        self.assertEqual(
            usage,
            {
                "input_tokens": 21,
                "output_tokens": 7,
                "reasoning_tokens": 3,
                "total_tokens": 31,
            },
        )
        self.assertTrue(all(PRIVATE_THOUGHT not in text for text in progress))
        self.assertTrue(all("PRIVATE_STRUCTURED_ARG" not in text for text in progress))
        self.assertTrue(all("PRIVATE_STRUCTURED_ARG" not in str(event) for event in events))
        self.assertEqual(process.commands[1]["type"], "set_auto_retry")
        self.assertIs(process.commands[1]["enabled"], False)
        prompt = process.commands[2]
        self.assertEqual(prompt["type"], "prompt")
        self.assertIn("Test task", prompt["message"])
        self.assertNotIn(SCHEMA, prompt["message"])
        command = popen.call_args.args[0]
        self.assertNotIn("Test task", command)
        self.assertIn("--tools", command)
        self.assertIn(pi_cli.STRUCTURED_OUTPUT_TOOL_NAME, command)
        self.assertIn("--no-builtin-tools", command)
        self.assertIn("--no-extensions", command)
        self.assertIn("--extension", command)
        self.assertNotIn("--no-tools", command)
        self.assertIn("--no-session", command)
        self.assertIn("--provider", command)
        self.assertIn("--model", command)
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertEqual(popen.call_args.kwargs["env"][pi_cli.STRUCTURED_OUTPUT_SCHEMA_ENV], SCHEMA)
        self.assertNotIn("NAYOSE_API_TOKEN", popen.call_args.kwargs["env"])

    def test_pi_rpc_rejects_invalid_typed_output_without_retry_or_leak(self):
        process = FakeProcess("invalid_structured_output")
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            runtime_patches[1],
            runtime_patches[2],
            self.assertRaisesRegex(RuntimeError, "constraint=required") as raised,
        ):
            pi_cli.complete_json("Test task", SCHEMA)

        self.assertEqual(sum(command.get("type") == "prompt" for command in process.commands), 1)
        self.assertNotIn("PRIVATE_INVALID_OUTPUT", str(raised.exception))

    def test_pi_rpc_rejects_unstructured_text_after_completed_response(self):
        process = FakeProcess("invalid_json")
        events = []
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            runtime_patches[1],
            runtime_patches[2],
            self.assertRaisesRegex(pi_cli.PiCLIError, "without the typed output tool") as raised,
        ):
            pi_cli.complete_json("Test task", SCHEMA, on_event=events.append)

        self.assertTrue(any(event["type"] == "finish" for event in events))
        self.assertNotIn("PRIVATE_RESPONSE_TEXT", str(raised.exception))

    def test_harness_reports_safe_missing_tool_telemetry(self):
        process = FakeProcess("invalid_json")
        progress = []
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            runtime_patches[1],
            runtime_patches[2],
            patch.object(
                llm_harness,
                "report_node_progress",
                lambda _node_id, text: progress.append(text),
            ),
            self.assertRaisesRegex(pi_cli.PiCLIError, "without the typed output tool") as raised,
        ):
            LLMRunHarness("node-1", "Fuzzy Math · Solver").run(pi_cli.complete_json, "task", SCHEMA)

        self.assertIn("finish_reason=stop", " ".join(progress))
        self.assertIn("answer_chars=", " ".join(progress))
        self.assertIn("failure_category=pi_cli_error", " ".join(progress))
        self.assertIn("details withheld", " ".join(progress))
        self.assertNotIn("PRIVATE_RESPONSE_TEXT", " ".join(progress))
        self.assertNotIn("PRIVATE_RESPONSE_TEXT", str(raised.exception))

    def test_harness_reports_typed_output_without_exposing_arguments(self):
        process = FakeProcess("structured_output")
        progress = []
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            runtime_patches[1],
            runtime_patches[2],
            patch.object(
                llm_harness,
                "report_node_progress",
                lambda _node_id, text: progress.append(text),
            ),
        ):
            result = LLMRunHarness("node-1", "Fuzzy Math · Solver").run(
                pi_cli.complete_json, "task", SCHEMA
            )

        self.assertEqual(result, {"answer": "PRIVATE_STRUCTURED_ARG"})
        self.assertTrue(any("typed output received" in text for text in progress))
        self.assertTrue(any("passed local schema validation" in text for text in progress))
        self.assertTrue(all("PRIVATE_STRUCTURED_ARG" not in text for text in progress))

    def test_pi_rpc_rejects_tool_calls_without_exposing_arguments(self):
        process = FakeProcess("tool")
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            runtime_patches[1],
            runtime_patches[2],
            self.assertRaisesRegex(pi_cli.PiCLIError, "tool call"),
        ):
            pi_cli.complete_json("Test task", SCHEMA)

    def test_pi_rpc_times_out_on_reasoning_without_answer_text(self):
        process = FakeProcess("reasoning_only")
        events = []
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            runtime_patches[1],
            runtime_patches[2],
            self.assertRaisesRegex(pi_cli.PiReasoningBudgetExceeded, "reasoning_events=1"),
        ):
            pi_cli.complete_json(
                "Test task",
                SCHEMA,
                reasoning_timeout_seconds=0.1,
                on_event=events.append,
            )
        self.assertTrue(any(event["type"] == "reasoning_budget_exceeded" for event in events))
        self.assertNotIn(PRIVATE_THOUGHT, str(events))

    def test_pi_rpc_requires_provider_and_model_as_a_pair(self):
        with (
            patch.object(pi_cli.shutil, "which", return_value="pi"),
            patch.dict("os.environ", {"PI_PROVIDER": "test-provider"}, clear=True),
            self.assertRaisesRegex(pi_cli.PiCLIError, "Set both PI_PROVIDER and PI_MODEL"),
        ):
            pi_cli._build_command()

    def test_studio_keychain_config_isolates_credentials_and_pi_profile(self):
        process = FakeProcess("structured_output")
        events = []
        runtime_patches = self._patch_runtime(process)
        with (
            runtime_patches[0],
            patch.dict(
                "os.environ",
                {
                    "TEST_PROVIDER_KEY": "ambient-value",
                    "OTHER_PROVIDER_SECRET": "unrelated-value",
                },
            ),
            patch.object(
                pi_cli,
                "all_provider_api_key_env_names",
                return_value=("OTHER_PROVIDER_SECRET", "TEST_PROVIDER_KEY"),
            ),
            patch.object(
                pi_cli,
                "provider_credential_environment",
                return_value={"TEST_PROVIDER_KEY": "keychain-value"},
            ),
            runtime_patches[2] as popen,
        ):
            pi_cli.complete_json(
                "Test task",
                SCHEMA,
                pi_config={
                    "version": 1,
                    "provider": "test-provider",
                    "model": "test-model",
                    "thinking_level": "high",
                    "credential_source": "studio_keychain",
                },
                on_event=events.append,
            )

        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["TEST_PROVIDER_KEY"], "keychain-value")
        self.assertNotIn("OTHER_PROVIDER_SECRET", environment)
        self.assertFalse(Path(environment["PI_CODING_AGENT_DIR"]).exists())
        self.assertTrue(
            any(
                event.get("type") == "auth_source"
                and event.get("auth_source") == "studio_keychain"
                and event.get("credential_fields") == ("TEST_PROVIDER_KEY",)
                for event in events
            )
        )
        self.assertNotIn("keychain-value", str(events))
        self.assertNotIn("keychain-value", str(popen.call_args.args[0]))
        self.assertIn("--provider", popen.call_args.args[0])
        self.assertIn("--model", popen.call_args.args[0])

    def test_auth_check_is_local_and_never_requests_credential_output(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"status":"ready","authType":"api_key"}'
        )
        with (
            patch.object(pi_cli.shutil, "which", return_value="/usr/bin/pi"),
            patch.object(pi_cli.subprocess, "run", return_value=completed) as run,
        ):
            result = pi_cli.check_auth("test-provider", "test-model", "pi_managed")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["auth_method"], "api_key")
        self.assertFalse(result["network_requested"])
        command = run.call_args.args[0]
        self.assertIn("--no-refresh", command)
        self.assertNotIn("--credentials", command)
        self.assertIs(run.call_args.kwargs["shell"], False)


if __name__ == "__main__":
    unittest.main()


__all__ = ["PiCliTests"]
