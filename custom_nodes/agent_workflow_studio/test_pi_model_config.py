import unittest

from .pi_model_config import (
    PI_MODEL_CONFIG_TYPE,
    AgentWorkflowPiModelConfig,
    validate_pi_model_config,
)


class PiModelConfigTests(unittest.TestCase):
    def test_versioned_config_is_validated_and_emitted(self):
        config = {
            "version": 1,
            "provider": "openai-codex",
            "model": "gpt-6-luna",
            "thinking_level": "high",
            "credential_source": "pi_managed",
        }
        self.assertEqual(validate_pi_model_config(config), config)
        output = AgentWorkflowPiModelConfig.execute(
            "openai-codex", "gpt-6-luna", "high", "pi_managed"
        )
        self.assertEqual(output.result, (config,))
        self.assertIn("PI MODEL CONFIG · VALID", output.ui["text"][0])
        self.assertIn("Provider: openai-codex", output.ui["text"][0])
        self.assertNotIn("api_key", output.ui["text"][0])
        self.assertEqual(
            AgentWorkflowPiModelConfig.define_schema().outputs[0].io_type,
            PI_MODEL_CONFIG_TYPE,
        )

    def test_config_rejects_invalid_versions_and_identifiers(self):
        with self.assertRaisesRegex(TypeError, "PI_MODEL_CONFIG_V1"):
            validate_pi_model_config(
                {
                    "version": True,
                    "provider": "provider",
                    "model": "model",
                    "thinking_level": "high",
                    "credential_source": "pi_managed",
                }
            )
        with self.assertRaisesRegex(ValueError, "valid Pi ID"):
            AgentWorkflowPiModelConfig.execute("provider;bad", "model", "high", "pi_managed")

    def test_config_node_is_registered_with_the_native_extension(self):
        import asyncio

        from . import AgentWorkflowStudioExtension

        node_types = asyncio.run(AgentWorkflowStudioExtension().get_node_list())
        self.assertIn(AgentWorkflowPiModelConfig, node_types)

    def test_config_node_exposes_optional_typed_input_on_the_agent_node(self):
        from .agent_node import PiStructuredAgent

        pi_input = next(
            item for item in PiStructuredAgent.define_schema().inputs if item.id == "pi_config"
        )
        self.assertEqual(pi_input.io_type, PI_MODEL_CONFIG_TYPE)
        self.assertTrue(pi_input.optional)


if __name__ == "__main__":
    unittest.main()
