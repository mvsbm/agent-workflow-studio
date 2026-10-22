"""The chat's environment prompt stays consistent with the agent's tools (agent_tools.json)."""

# Verifies: R12
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "custom_nodes" / "agent_workflow_studio"
PROMPT = (HERE / "prompts" / "comfyui_agent.md").read_text()
TOOLS = json.loads((HERE / "agent_tools.json").read_text())
EXTENSION = (HERE / "pi_chat_extension.ts").read_text()


class AgentPromptTests(unittest.TestCase):
    def test_every_registered_comfy_tool_is_documented(self) -> None:
        registered = {tool["name"] for tool in TOOLS}
        self.assertEqual(len(registered), 21)
        for tool in registered:
            with self.subTest(tool=tool):
                self.assertIn(f"`{tool}`", PROMPT)

    def test_every_skill_header_parses(self) -> None:
        # Pi refuses to start if a bundled skill's YAML header does not parse (e.g. an unquoted ": ").
        import yaml

        for skill in sorted((HERE / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=skill.parent.name):
                header = yaml.safe_load(skill.read_text().split("---")[1])
                self.assertEqual(header["name"], skill.parent.name)
                self.assertIsInstance(header["description"], str)

    def test_documented_edit_operations_match_the_schema(self) -> None:
        edit = next(t for t in TOOLS if t["name"] == "comfy_edit_workflow")
        variants = edit["parameters"]["properties"]["operations"]["items"]["anyOf"]
        operations = {v["properties"]["op"]["const"] for v in variants}
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

    def test_the_extension_expects_exactly_the_bundled_skills(self) -> None:
        # Pi refuses to start when the count differs, so a new skill must update both.
        from custom_nodes.agent_workflow_studio.pi_chat import REQUIRED_SKILLS

        skills = {p.parent.name for p in (HERE / "skills").glob("*/SKILL.md")}
        self.assertEqual(skills, REQUIRED_SKILLS)
        expected = int(re.search(r"loaded\.skills\.length!==(\d+)", EXTENSION).group(1))
        self.assertEqual(expected, len(skills))

    def test_placeholders_are_filled_by_the_extension(self) -> None:
        for placeholder in re.findall(r"\{\{[A-Z_]+\}\}", PROMPT):
            with self.subTest(placeholder=placeholder):
                self.assertIn(f'replaceAll("{placeholder}"', EXTENSION)  # Pi

    def test_claude_code_gets_the_prompt_filled_in(self) -> None:
        from custom_nodes.agent_workflow_studio.claude_chat import system_prompt

        prompt = system_prompt("WF", "/workspace/dir")
        self.assertNotRegex(prompt, r"\{\{[A-Z_]+\}\}")
        self.assertIn("/workspace/dir", prompt)

    def test_tool_schemas_avoid_tuples(self) -> None:
        # Array-form "items" (a tuple) is invalid JSON Schema 2020-12; providers reject it.
        def walk(value):
            if isinstance(value, dict):
                self.assertNotIsInstance(value.get("items"), list)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(TOOLS)

    def test_prompt_has_no_references_to_removed_features(self) -> None:
        for stale in ("node-development", "managed worker", "activation", "Nayose"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale.lower(), PROMPT.lower())


if __name__ == "__main__":
    unittest.main()
