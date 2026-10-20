"""The recurring stage patterns ship as native blueprints, in sync with the nayose reference, and
carry no one's contract (no aws_ tags)."""

import json
import unittest

from custom_nodes.agent_workflow_studio.blueprints import OUT, blueprints


class BlueprintTests(unittest.TestCase):
    def test_blueprints_are_current_and_carry_no_contract(self) -> None:
        wanted = blueprints()
        found = {p.name: p.read_text(encoding="utf-8") for p in OUT.glob("*.json")}
        regenerate = "regenerate: .venv/bin/python -m custom_nodes.agent_workflow_studio.blueprints"
        self.assertEqual(sorted(found), sorted(wanted), regenerate)
        self.assertEqual([n for n, t in wanted.items() if found[n] != t], [], regenerate)
        for text in wanted.values():
            self.assertNotIn('"aws_', text)
            self.assertEqual(len(json.loads(text)["nodes"]), 1)


if __name__ == "__main__":
    unittest.main()
