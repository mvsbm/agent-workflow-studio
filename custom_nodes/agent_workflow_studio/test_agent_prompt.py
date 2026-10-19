"""The chat's environment prompt stays consistent with the tools the extension registers."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).parent
PROMPT = (HERE / "prompts" / "comfyui_agent.md").read_text()
EXTENSION = (HERE / "pi_chat_extension.ts").read_text()
FILES = (HERE / "pi_chat_files.ts").read_text()


class AgentPromptTests(unittest.TestCase):
    def test_every_registered_comfy_tool_is_documented(self) -> None:
        registered = set(re.findall(r'name:"(comfy_[a-z_]+)"', EXTENSION + FILES))
        self.assertTrue(registered)
        for tool in registered:
            with self.subTest(tool=tool):
                self.assertIn(f"`{tool}`", PROMPT)

    def test_documented_edit_operations_match_the_schema(self) -> None:
        operations = set(re.findall(r'op: Type\.Literal\("([a-z]+)"\)', EXTENSION))
        self.assertEqual(
            operations,
            {
                "add",
                "move",
                "title",
                "widget",
                "connect",
                "remove",
                "disconnect",
                "arrange",
                "signature",
            },
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.assertIn(f"`{operation}`", PROMPT)

    def test_placeholders_are_filled_by_the_extension(self) -> None:
        for placeholder in re.findall(r"\{\{[A-Z_]+\}\}", PROMPT):
            with self.subTest(placeholder=placeholder):
                self.assertIn(f'replaceAll("{placeholder}"', EXTENSION)

    def test_tool_schemas_avoid_tuples(self) -> None:
        # Type.Tuple emits array-form "items", invalid JSON Schema 2020-12; providers reject it.
        self.assertNotIn("Type.Tuple", EXTENSION + FILES)

    def test_prompt_has_no_references_to_removed_features(self) -> None:
        for stale in ("node-development", "managed worker", "activation", "Nayose"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale.lower(), PROMPT.lower())


if __name__ == "__main__":
    unittest.main()
