"""Every node has a current help page for ComfyUI's Info tab (generated from its schema).

Verifies: R4
"""

import unittest

from custom_nodes.agent_workflow_studio.node_docs import DOCS, pages


class NodeDocsTests(unittest.TestCase):
    def test_every_node_has_a_current_help_page(self) -> None:
        wanted = pages()
        found = {p.stem: p.read_text(encoding="utf-8") for p in DOCS.glob("*.md")}
        regenerate = "regenerate: .venv/bin/python -m custom_nodes.agent_workflow_studio.node_docs"
        self.assertEqual(sorted(found), sorted(wanted), f"pages missing or extra; {regenerate}")
        stale = [name for name, text in wanted.items() if found.get(name) != text]
        self.assertEqual(stale, [], f"stale pages; {regenerate}")
        self.assertIn("## Inputs", wanted["AgentWorkflowWriteTable"])


if __name__ == "__main__":
    unittest.main()
