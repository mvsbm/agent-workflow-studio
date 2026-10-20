"""A step's LLD is accepted only when it realizes the whole HLD step and every node is traced."""

import copy
import unittest

from .lld import validate_lld
from .test_workflow_contract import CONTRACT

STEP = CONTRACT["steps"][0]  # classify: takes review (input.review), gives label
SPEC = {
    "nodes": {
        "llm": {
            "type": "AgentWorkflowPiStructuredAgent",
            "set": {"instructions": "positive or negative"},
            "realizes": ["takes review", "does"],
        },
        "pick": {"type": "AgentWorkflowJSONField", "realizes": ["gives label", "why"]},
    },
    "wires": [
        {"from": "input.review", "to": "llm.text"},
        {"from": "llm.result", "to": "pick.data"},
        {"from": "pick.value", "to": "output.label"},
    ],
}


def spec(change=None):
    out = copy.deepcopy(SPEC)
    if change:
        change(out)
    return out


class LLDTests(unittest.TestCase):
    def test_a_complete_traced_spec_is_accepted(self) -> None:
        built = validate_lld(STEP, spec())
        self.assertEqual(built["step"], "classify")
        self.assertEqual(built["nodes"]["llm"]["realizes"], ["takes review", "does"])

    def test_what_is_wrong_is_named(self) -> None:
        cases = {
            "No node realizes gives label": lambda s: s["nodes"]["pick"].update(realizes=["why"]),
            "realizes speed, which the design of classify does not say": lambda s: s["nodes"][
                "pick"
            ].update(realizes=["gives label", "speed"]),
            "say which part of the design it realizes": lambda s: s["nodes"]["pick"].pop(
                "realizes"
            ),
            "gives label, but no wire feeds it": lambda s: s["wires"].pop(),
            "takes review, but no wire uses it": lambda s: s["wires"].pop(0),
            "takes no folder": lambda s: s["wires"].append(
                {"from": "input.folder", "to": "llm.text"}
            ),
            "Wire to unknown node nope": lambda s: s["wires"].append(
                {"from": "llm.result", "to": "nope.x"}
            ),
            "fed twice": lambda s: s["wires"].append({"from": "llm.result", "to": "output.label"}),
        }
        for message, change in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                validate_lld(STEP, spec(change))

    def test_a_design_decision_may_be_realized(self) -> None:
        tagged = spec(lambda s: s["nodes"]["pick"]["realizes"].append("decision D1"))
        self.assertIn(
            "decision D1", validate_lld(STEP, tagged, ["D1"])["nodes"]["pick"]["realizes"]
        )


if __name__ == "__main__":
    unittest.main()
