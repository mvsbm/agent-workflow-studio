"""Offline tests for the Pi-backed structured agent node."""

from __future__ import annotations

import asyncio
import math
import unittest
from unittest.mock import patch

from . import AgentWorkflowStudioExtension, agent_node, llm_harness
from .agent_node import PiStructuredAgent

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
        self.assertEqual(ids, {"AgentWorkflowPiModelConfig", "AgentWorkflowPiStructuredAgent"})


if __name__ == "__main__":
    unittest.main()
