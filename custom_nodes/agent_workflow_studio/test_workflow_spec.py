"""The standardized workflow plan: validation and the plain-language approval view."""

from __future__ import annotations

import copy
import unittest

from .workflow_spec import render_spec, validate_spec

SPEC = {
    "goal": "Turn a product review into a one-word sentiment label.",
    "inputs": [{"name": "review", "description": "The review text", "type": "text"}],
    "outputs": [{"name": "label", "description": "positive or negative", "type": "text"}],
    "steps": [
        {
            "id": "classify",
            "purpose": "Decide whether the review is positive or negative.",
            "approach": "llm",
            "nodes": ["AgentWorkflowPiStructuredAgent"],
            "connections": "review -> prompt; validated_result -> Answer",
            "success_gate": {
                "description": "The answer is positive or negative.",
                "check": {"node_title": "Answer", "kind": "regex", "value": "positive|negative"},
            },
        },
        {
            "id": "show",
            "purpose": "Show the label.",
            "approach": "deterministic",
            "success_gate": {"description": "The label reads naturally."},
        },
    ],
    "assumptions": ["Reviews are in English."],
    "decisions": [{"question": "Neutral reviews?", "answer": "Treat as negative."}],
    "open_questions": [],
}


class WorkflowSpecTests(unittest.TestCase):
    def test_valid_plan(self) -> None:
        self.assertIs(validate_spec(SPEC), SPEC)

    def test_rejects_incomplete_plans(self) -> None:
        def broken(change):
            spec = copy.deepcopy(SPEC)
            change(spec)
            return spec

        cases = {
            "success_gate": broken(lambda s: s["steps"][0].pop("success_gate")),
            "unique id": broken(lambda s: s["steps"][1].update(id="classify")),
            "needs a value": broken(lambda s: s["steps"][0]["success_gate"]["check"].pop("value")),
            "open questions": broken(lambda s: s["open_questions"].append("Which language?")),
            "outputs": broken(lambda s: s.update(outputs=[])),
            "approach": broken(lambda s: s["steps"][0].update(approach="magic")),
            "kind": broken(lambda s: s["steps"][0]["success_gate"]["check"].update(kind="vibes")),
        }
        for message, spec in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                validate_spec(spec)
        with self.assertRaises(ValueError):
            validate_spec(None)

    def test_plain_view_first_technical_details_last(self) -> None:
        text = render_spec(SPEC)
        self.assertTrue(text.startswith("GOAL: Turn a product review"))
        self.assertIn("1. Decide whether the review is positive or negative. [AI judgment]", text)
        self.assertIn(
            "✓ Done when: The answer is positive or negative. (checked automatically:", text
        )
        self.assertIn("The label reads naturally. (you confirm this one)", text)
        self.assertIn("DECISIONS:\n  • Neutral reviews? → Treat as negative.", text)
        self.assertLess(text.index("STEPS:"), text.index("Technical details"))
        self.assertIn("classify: AgentWorkflowPiStructuredAgent — review -> prompt", text)


if __name__ == "__main__":
    unittest.main()
