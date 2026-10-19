"""Offline tests for the Pi-backed structured agent node."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import AgentWorkflowStudioExtension, agent_node, llm_harness
from .agent_node import PiStructuredAgent
from .project_repo import ProjectRepo

SCHEMA = (
    '{"type":"object","properties":{"answer":{"type":"string"}},'
    '"required":["answer"],"additionalProperties":false}'
)


class PiStructuredAgentTests(unittest.TestCase):
    def test_emits_native_dict_through_pi_cli(self) -> None:
        seen = {}

        def fake_complete(task, schema, **kwargs):
            seen.update(task=task, schema=schema, kwargs=kwargs)
            return {"answer": "ok"}

        with (
            patch.object(agent_node, "complete_json", fake_complete),
            patch.object(llm_harness, "report_node_progress"),
        ):
            output = PiStructuredAgent.execute("connected prompt", SCHEMA, 0.0, 128)

        self.assertEqual(output.args, ({"answer": "ok"},))
        self.assertEqual(seen["task"], "connected prompt")
        self.assertNotIn("api_key", seen["kwargs"])
        self.assertEqual(seen["kwargs"]["reasoning_timeout_seconds"], 120)
        self.assertEqual(PiStructuredAgent.define_schema().outputs[0].io_type, "DICT")

    def test_preview_marks_validated_value_without_echoing_it(self) -> None:
        with (
            patch.object(agent_node, "complete_json", return_value={"answer": "ok"}),
            patch.object(llm_harness, "report_node_progress"),
        ):
            output = PiStructuredAgent.execute("prompt", SCHEMA, 0.0, 32)

        text = output.ui["text"][0]
        self.assertIn("STRUCTURED RESULT · SCHEMA VALIDATED", text)
        self.assertIn("Top-level fields: 1", text)
        self.assertNotIn('"answer"', text)

    def test_json_inputs_disable_dynamic_prompts_and_cache_is_off(self) -> None:
        inputs = {i.id: i for i in PiStructuredAgent.define_schema().inputs}
        self.assertFalse(inputs["output_schema_json"].dynamic_prompts)
        self.assertTrue(inputs["prompt"].dynamic_prompts)
        self.assertTrue(math.isnan(PiStructuredAgent.fingerprint_inputs()))

    def test_extension_registers_exactly_the_studio_nodes(self) -> None:
        node_types = asyncio.run(AgentWorkflowStudioExtension().get_node_list())
        ids = {node.define_schema().node_id for node in node_types}
        self.assertEqual(
            ids,
            {
                "AgentWorkflowPiModelConfig",
                "AgentWorkflowPiStructuredAgent",
                "AgentWorkflowFuzzyNode",
                "AgentWorkflowAgentTool",
                "AgentWorkflowDecisionRules",
                "AgentWorkflowProfileData",
                "AgentWorkflowCheck",
                "AgentWorkflowApiOutput",
                *(
                    f"AgentWorkflow{name}"
                    for name in (
                        "Stub SplitName ReadTable WriteTable StackRoles Concatenate FilterRows JoinTables NormalizeText "
                        "MapValues ParseDates ExtractDigits FindCandidates CompareFields AttachGroups "
                        "ParseNumbers RemoveDuplicates ComputeColumn Summarize DataChecks"
                    ).split()
                ),
                "AgentWorkflowLearnWeights",
                "AgentWorkflowLLMSuggestions",
                "AgentWorkflowReviewDecisions",
                "AgentWorkflowClusterGuard",
                "AgentWorkflowNayosePeople",
                "AgentWorkflowNayoseActivity",
                "AgentWorkflowNayoseExport",
                "AgentWorkflowNayoseScore",
                "AgentWorkflowPreviewTable",
            },
        )


class RunRecordTests(unittest.TestCase):
    WORKFLOW = {"id": "5d9a308c-81ae-4aab-a0bd-5145aa83e56e", "nodes": [{"id": 7, "type": "X"}]}

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = ProjectRepo(root, self.WORKFLOW["id"])
        hidden = SimpleNamespace(unique_id="7", extra_pnginfo={"workflow": self.WORKFLOW})
        self.patches = [
            patch.object(agent_node, "projects_root", return_value=root),
            patch.object(agent_node, "_build_command", return_value=([], "prov", "mod", "low")),
            patch.object(PiStructuredAgent, "hidden", hidden, create=True),
            patch.object(llm_harness, "report_node_progress"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def config(self):
        return {
            "version": 1,
            "provider": "prov",
            "model": "mod",
            "thinking_level": "low",
            "credential_source": "studio_keychain",
        }

    def test_success_is_committed_with_the_workflow_that_ran(self) -> None:
        with patch.object(agent_node, "complete_json", return_value={"answer": "ok"}):
            output = PiStructuredAgent.execute("classify this", SCHEMA, 0.0, 64, "", self.config())
        log = self.repo.log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["author"], "agent")
        self.assertEqual(log[0]["message"], "Run · Pi CLI Structured LLM #7 · succeeded")
        self.assertIn(f"Run record: version {log[0]['sha'][:8]}", output.ui["text"][0])
        record = self.repo.record(log[0]["sha"])
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["output"], {"answer": "ok"})
        self.assertEqual(record["input"]["prompt"], "classify this")
        self.assertEqual(record["input"]["output_schema"]["required"], ["answer"])
        self.assertEqual(
            record["model"],
            {
                "provider": "prov",
                "model": "mod",
                "thinking_level": "low",
                "credential_source": "studio_keychain",
            },
        )
        self.assertEqual(self.repo.show(log[0]["sha"]), self.WORKFLOW)
        self.assertNotIn("api_key", json.dumps(record).lower())

    def test_failure_is_recorded_by_category_and_still_raised(self) -> None:
        with (
            patch.object(
                agent_node, "complete_json", side_effect=TimeoutError("provider secret detail")
            ),
            self.assertRaises(TimeoutError),
        ):
            PiStructuredAgent.execute("x", SCHEMA, 0.0, 64)
        record = self.repo.record(self.repo.log()[0]["sha"])
        self.assertEqual(
            (record["status"], record["error"], record["output"]), ("failed", "timeout", None)
        )
        self.assertNotIn("secret detail", json.dumps(record))

    def test_run_without_a_workflow_is_not_recorded(self) -> None:
        with (
            patch.object(
                PiStructuredAgent, "hidden", SimpleNamespace(unique_id="7", extra_pnginfo=None)
            ),
            patch.object(agent_node, "complete_json", return_value={"answer": "ok"}),
        ):
            output = PiStructuredAgent.execute("x", SCHEMA, 0.0, 64)
        self.assertIn("Run record: not saved", output.ui["text"][0])
        self.assertEqual(self.repo.log(), [])

    def test_ordinary_versions_have_no_record(self) -> None:
        sha = self.repo.commit({"nodes": []}, "Save", "user")
        self.assertIsNone(self.repo.record(sha))


if __name__ == "__main__":
    unittest.main()
