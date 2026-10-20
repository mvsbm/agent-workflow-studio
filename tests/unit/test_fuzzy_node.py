"""Fuzzy node: plan validation against live-schema shapes, expansion, and the node."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from custom_nodes.agent_workflow_studio import fuzzy_node, llm_harness
from custom_nodes.agent_workflow_studio.fuzzy_node import FuzzyNode, expand_plan, validate_plan
from custom_nodes.agent_workflow_studio.project_repo import ProjectRepo

TEXT = ["STRING", {"multiline": True}]
SCHEMAS = {  # shapes as returned by /object_info for V3 and legacy nodes
    "CaseConverter": {
        "input": {
            "required": {"string": TEXT, "mode": ["COMBO", {"options": ["UPPERCASE", "lowercase"]}]}
        },
        "output": ["STRING"],
    },
    "StringReplace": {
        "input": {"required": {"string": TEXT, "find": TEXT, "replace": TEXT}},
        "output": ["STRING"],
    },
    "StringLength": {"input": {"required": {"string": TEXT}}, "output": ["INT"]},
    "LegacyPick": {
        "input": {
            "required": {"choice": [["a", "b"], {}]},
            "optional": {"count": ["INT", {"min": 0, "max": 5}]},
        },
        "output": ["STRING"],
    },
    "NeedsImage": {"input": {"required": {"image": ["IMAGE", {}]}}, "output": ["STRING"]},
}
UPPER = {
    "explanation": "Uppercase, then swap spaces for dashes.",
    "steps": [
        {
            "id": "upper",
            "class_type": "CaseConverter",
            "inputs": {"string": {"from": "$input"}, "mode": "UPPERCASE"},
        },
        {
            "id": "dash",
            "class_type": "StringReplace",
            "inputs": {"string": {"from": "upper", "output": 0}, "find": " ", "replace": "-"},
        },
    ],
    "output": {"from": "dash", "output": 0},
}


def step(sid, class_type, **inputs):
    return {"id": sid, "class_type": class_type, "inputs": inputs}


class ValidatePlanTests(unittest.TestCase):
    def test_valid_plans(self) -> None:
        validate_plan(UPPER, SCHEMAS)
        validate_plan({"explanation": "", "steps": [], "output": {"from": "$input"}}, SCHEMAS)
        validate_plan(
            {
                "explanation": "",
                "steps": [step("p", "LegacyPick", choice="b", count=3)],
                "output": {"from": "p"},
            },
            SCHEMAS,
        )

    def test_rejects_plans_a_model_could_get_wrong(self) -> None:
        upper = step("u", "CaseConverter", string={"from": "$input"}, mode="UPPERCASE")
        cases = {
            "not an allowed node": [step("x", "DeleteFiles", path="/")],
            "has no input": [
                step("u", "CaseConverter", string={"from": "$input"}, mode="UPPERCASE", size=1)
            ],
            "missing required input": [step("u", "CaseConverter", string={"from": "$input"})],
            "is not one of": [step("u", "CaseConverter", string={"from": "$input"}, mode="SHOUT")],
            "unknown or later step": [
                step("u", "CaseConverter", string={"from": "later"}, mode="UPPERCASE")
            ],
            "cannot connect INT to STRING": [
                step("n", "StringLength", string={"from": "$input"}),
                step("u", "CaseConverter", string={"from": "n"}, mode="UPPERCASE"),
            ],
            "has no output 1": [
                upper,
                step(
                    "r", "StringReplace", string={"from": "u", "output": 1}, find="a", replace="b"
                ),
            ],
            "must be connected, not literal": [step("i", "NeedsImage", image="x.png")],
            "expected text": [step("u", "CaseConverter", string=5, mode="UPPERCASE")],
            "out of range": [step("p", "LegacyPick", choice="a", count=9)],
            "duplicate step id": [upper, upper],
        }
        for message, steps in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                validate_plan(
                    {"explanation": "", "steps": steps, "output": {"from": steps[0]["id"]}}, SCHEMAS
                )
        with self.assertRaisesRegex(ValueError, "output must be text"):
            validate_plan(
                {
                    "explanation": "",
                    "steps": [step("n", "StringLength", string={"from": "$input"})],
                    "output": {"from": "n"},
                },
                SCHEMAS,
            )
        with self.assertRaisesRegex(ValueError, "at most"):
            validate_plan(
                {"explanation": "", "steps": [upper] * 13, "output": {"from": "$input"}}, SCHEMAS
            )


class ExpandPlanTests(unittest.TestCase):
    def test_expands_into_linked_native_nodes(self) -> None:
        result, graph = expand_plan(UPPER, "hello world")
        nodes = list(graph.values())
        self.assertEqual([n["class_type"] for n in nodes], ["CaseConverter", "StringReplace"])
        upper_id = list(graph)[0]
        self.assertEqual(nodes[0]["inputs"], {"string": "hello world", "mode": "UPPERCASE"})
        self.assertEqual(nodes[1]["inputs"]["string"], [upper_id, 0])
        self.assertEqual(result, [list(graph)[1], 0])

    def test_identity_plan_needs_no_nodes(self) -> None:
        self.assertEqual(
            expand_plan({"steps": [], "output": {"from": "$input"}}, "same"), ("same", None)
        )


class FuzzyNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        workflow = {"id": "5d9a308c-81ae-4aab-a0bd-5145aa83e56e", "nodes": []}
        self.repo = ProjectRepo(self.root, workflow["id"])
        self.patches = [
            patch.object(fuzzy_node, "node_schema", side_effect=SCHEMAS.get),
            patch.object(llm_harness, "report_node_progress"),
            patch(
                "custom_nodes.agent_workflow_studio.agent_node.projects_root",
                return_value=self.root,
            ),
            patch.object(
                FuzzyNode,
                "hidden",
                SimpleNamespace(unique_id="4", extra_pnginfo={"workflow": workflow}),
                create=True,
            ),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_valid_plan_expands_and_is_recorded(self) -> None:
        with patch.object(fuzzy_node, "complete_json", return_value=UPPER) as model:
            output = FuzzyNode.execute(
                "shout it, dashes for spaces", "hello world", "CaseConverter, StringReplace"
            )
        request = model.call_args.args[0]
        self.assertIn("FUZZY PLAN REQUEST", request)
        self.assertIn('"CaseConverter"', request)
        self.assertNotIn('"StringLength"', request)
        self.assertEqual(output.args[1], UPPER)
        self.assertEqual(output.ui["aws_plan"], (UPPER,))
        self.assertEqual(
            [n["class_type"] for n in output.expand.values()], ["CaseConverter", "StringReplace"]
        )
        self.assertIn("2. dash = StringReplace(string=upper[0]", output.ui["text"][0])
        log = self.repo.log()
        self.assertEqual(log[0]["message"], "Run · Fuzzy plan #4 · succeeded")
        record = self.repo.record(log[0]["sha"])
        self.assertEqual((record["kind"], record["output"]), ("fuzzy_plan", UPPER))

    def test_invalid_plan_fails_and_is_recorded(self) -> None:
        bad = {
            **UPPER,
            "steps": [step("x", "StringLength", string={"from": "$input"})],
            "output": {"from": "x"},
        }
        with (
            patch.object(fuzzy_node, "complete_json", return_value=bad),
            self.assertRaisesRegex(ValueError, "not an allowed"),
        ):
            FuzzyNode.execute("count it", "abc", "CaseConverter")
        record = self.repo.record(self.repo.log()[0]["sha"])
        self.assertEqual(
            (record["status"], record["error"]), ("failed", "input_or_schema_validation")
        )

    def test_rejects_uninstalled_allowed_nodes_before_calling_the_model(self) -> None:
        with (
            patch.object(fuzzy_node, "complete_json") as model,
            self.assertRaisesRegex(ValueError, "not installed: Nope"),
        ):
            FuzzyNode.execute("x", "y", "CaseConverter, Nope")
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
