# Verifies: R4
from __future__ import annotations

import asyncio
import unittest

from custom_nodes.agent_workflow_studio import AgentWorkflowStudioExtension


class NativeNodeSchemaMetadataTests(unittest.TestCase):
    def test_registered_nodes_have_descriptions_and_guided_inputs(self) -> None:
        node_types = asyncio.run(AgentWorkflowStudioExtension().get_node_list())
        for node_type in node_types:
            schema = node_type.define_schema()
            self.assertTrue(schema.description, schema.node_id)
            for input_socket in schema.inputs:
                with self.subTest(node=schema.node_id, input=input_socket.id):
                    self.assertTrue(input_socket.display_name)
                    self.assertTrue(input_socket.tooltip)
            for output_socket in schema.outputs:
                with self.subTest(node=schema.node_id, output=output_socket.id):
                    self.assertTrue(output_socket.display_name)


if __name__ == "__main__":
    unittest.main()
