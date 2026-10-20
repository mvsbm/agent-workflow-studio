"""A workflow's CI run: each test level proves one layer, and only a green run on this graph counts."""

# Verifies: R14, R35

import copy
import unittest

from custom_nodes.agent_workflow_studio.native_layers import embed
from custom_nodes.agent_workflow_studio.pipeline import run
from custom_nodes.agent_workflow_studio.workflow_api import prompt_hash

from .test_native_layers import workflow
from .test_workflow_contract import CONTRACT

PROMPT = {"1": {"class_type": "X", "inputs": {}}}


def built() -> dict:
    """The contract's two stages, each wired port to port through one tagged node."""
    stored = embed(CONTRACT, workflow())
    for definition, takes in zip(
        stored["definitions"]["subgraphs"], ("review", "label"), strict=True
    ):
        definition.update(
            inputs=[{"name": takes}],
            outputs=[{"name": "label"}],
            nodes=[
                {
                    "id": 1,
                    "type": "Work",
                    "properties": {"aws_realizes": [f"takes {takes}", "does", "gives label"]},
                }
            ],
            links=[
                {"origin_id": -10, "origin_slot": 0, "target_id": 1, "target_slot": 0},
                {"origin_id": 1, "origin_slot": 0, "target_id": -20, "target_slot": 0},
            ],
        )
    # A complete App: the promised input asked for, the promised output shown, nothing loose.
    stored["nodes"] = [n for n in stored["nodes"] if n["type"] != "PreviewAny"] + [
        {"id": 6, "type": "PrimitiveString", "properties": {"aws_input": "review"}},
        {"id": 7, "type": "AgentWorkflowApiOutput", "properties": {"aws_output": "label"}},
    ]
    stored["extra"]["linearData"] = {"inputs": [["6", "value"]], "outputs": ["7"]}
    return stored


def evidence(gates: dict | None = None) -> dict:
    return {
        "workflow": built(),
        "prompt": PROMPT,
        "nodes": [{"title": "Work", "step": "classify"}, {"title": "Work", "step": "tidy"}],
        "gate_results": gates or {},
        "run": {"current": True} if gates else None,
    }


def accepted(passed: bool, prompt: dict = PROMPT) -> dict:
    return {
        "kind": "acceptance",
        "prompt_hash": prompt_hash(prompt),
        "passed": int(passed),
        "total": 1,
        "results": [
            {"example": "E1", "passed": passed, "misses": [] if passed else ["label: got neutral"]}
        ],
    }


def statuses(result: dict) -> dict:
    return {s["stage"]: s["status"] for s in result["stages"]}


class PipelineTests(unittest.TestCase):
    def test_nothing_run_yet_is_not_green(self) -> None:
        result = run(CONTRACT, evidence(), [], None)
        self.assertEqual(
            statuses(result),
            {
                "static": "passed",
                "integration": "not_run",
                "acceptance": "not_run",
                "regression": "passed",
            },
        )
        self.assertFalse(result["green"])
        self.assertEqual(
            result["summary"], "✓ design · ○ stage Checks · ○ examples · ✓ no regression"
        )

    def test_green_needs_the_checks_and_examples_of_exactly_this_graph(self) -> None:
        gates = {"classify": "passed", "tidy": "needs_user_review"}
        self.assertTrue(run(CONTRACT, evidence(gates), [accepted(True)], None)["green"])
        older = accepted(True, {"1": {"class_type": "Y", "inputs": {}}})  # another graph's examples
        self.assertEqual(
            statuses(run(CONTRACT, evidence(gates), [older], None))["acceptance"], "not_run"
        )
        failed = run(CONTRACT, evidence(gates), [accepted(False)], None)
        self.assertEqual(failed["stages"][2]["detail"], ["E1: label: got neutral"])

    def test_a_hand_edit_that_breaks_the_lld_fails_the_design_stage(self) -> None:
        broken = evidence({"classify": "passed"})
        broken["workflow"]["definitions"]["subgraphs"][0]["links"].pop()  # output no longer fed
        result = run(CONTRACT, broken, [accepted(True)], None)
        self.assertEqual(statuses(result)["static"], "failed")
        self.assertIn(
            "1 · Classify: gives label, but nothing inside feeds it", result["stages"][0]["detail"]
        )

    def test_what_passed_in_the_published_version_must_still_pass(self) -> None:
        published = {"semver": "1.0.0", "checks": {"examples": ["E1"], "gates": ["classify"]}}
        gates = {"classify": "failed", "tidy": "needs_user_review"}
        result = run(copy.deepcopy(CONTRACT), evidence(gates), [accepted(True)], published)
        self.assertEqual(statuses(result)["regression"], "failed")
        self.assertEqual(result["stages"][3]["detail"], ["classify's Checks passed in 1.0.0"])


if __name__ == "__main__":
    unittest.main()
