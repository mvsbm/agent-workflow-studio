"""The contract, HLD and LLD live in the ComfyUI workflow itself and read back unchanged."""

# Verifies: R10, R28
import copy
import unittest

from custom_nodes.agent_workflow_studio.native_layers import embed, layers, split

from .test_workflow_contract import CONTRACT


def workflow() -> dict:
    """Two stage nodes (as the skeleton makes them) and their subgraph definitions."""
    return {
        "nodes": [
            {"id": 3, "type": "sg-a", "properties": {"aws_stage": "classify"}},
            {"id": 4, "type": "sg-b", "properties": {"aws_stage": "tidy"}},
            {"id": 5, "type": "PreviewAny", "properties": {}},
        ],
        "definitions": {
            "subgraphs": [
                {"id": "sg-a", "name": "1 · Classify", "nodes": []},
                {"id": "sg-b", "name": "2 · Tidy", "nodes": []},
            ]
        },
        "extra": {"ds": {"scale": 1}},
    }


class NativeLayersTests(unittest.TestCase):
    def test_a_contract_stored_in_the_workflow_reads_back_the_same(self) -> None:
        stored = embed(CONTRACT, workflow(), {"classify": "takes review → Decide …"})
        self.assertEqual(layers(stored), CONTRACT)
        # Where each layer lives: intent in extra, each step on its stage, its sentence natively.
        self.assertNotIn("steps", stored["extra"]["aws_contract"])
        self.assertEqual(stored["nodes"][0]["properties"]["aws_step"]["id"], "classify")
        self.assertEqual(
            stored["definitions"]["subgraphs"][0]["description"], "takes review → Decide …"
        )
        self.assertEqual(stored["extra"]["ds"], {"scale": 1})  # the canvas's own data is kept
        self.assertNotIn("aws_contract", workflow()["extra"])  # the input is not changed

    def test_user_decisions_are_intent_and_the_agents_are_design(self) -> None:
        contract = copy.deepcopy(CONTRACT)
        contract["decisions"].append(
            {"id": "D2", "decision": "Rules first", "why": "cheap", "by": "agent"}
        )
        intent, design, _ = split(contract)
        self.assertEqual([d["id"] for d in intent["decisions"]], ["D1"])
        self.assertEqual([d["id"] for d in design["decisions"]], ["D2"])
        self.assertEqual(design["order"], ["classify", "tidy"])

    def test_a_removed_stage_is_missing_not_forgotten(self) -> None:
        stored = embed(CONTRACT, workflow())
        stored["nodes"] = [n for n in stored["nodes"] if n["id"] != 4]
        self.assertEqual(layers(stored)["steps"][1], {"id": "tidy", "missing": True})
        self.assertIsNone(layers(workflow()))  # no contract: nothing is made up

    def test_untagged_stage_nodes_get_tags_from_their_wiring(self) -> None:
        from custom_nodes.agent_workflow_studio.native_layers import infer_realizes

        stored = workflow()
        stored["definitions"]["subgraphs"][0].update(
            inputs=[{"name": "review"}],
            outputs=[{"name": "label"}],
            nodes=[
                {"id": 1, "type": "ReadText"},
                {"id": 2, "type": "Classify", "properties": {"aws_realizes": ["does", "why"]}},
                {"id": 3, "type": "AgentWorkflowCheck"},
            ],
            links=[
                {"origin_id": -10, "origin_slot": 0, "target_id": 1, "target_slot": 0},
                {"origin_id": 1, "origin_slot": 0, "target_id": 2, "target_slot": 0},
                {"origin_id": 1, "origin_slot": 0, "target_id": -20, "target_slot": 0},
            ],
        )
        nodes = infer_realizes(stored)["definitions"]["subgraphs"][0]["nodes"]
        self.assertEqual(
            nodes[0]["properties"]["aws_realizes"], ["gives label", "takes review", "does"]
        )
        self.assertEqual(
            nodes[1]["properties"]["aws_realizes"], ["does", "why"]
        )  # an author's tag stays
        self.assertNotIn("properties", nodes[2])  # scaffolding is not part of the design

    def test_a_setting_promoted_to_the_stage_card_is_not_data_the_design_takes(self) -> None:
        from custom_nodes.agent_workflow_studio.native_layers import infer_realizes

        stored = workflow()
        stored["nodes"][0]["properties"]["aws_step"] = {"takes": {"pairs": "compare.pairs"}}
        stored["definitions"]["subgraphs"][0].update(
            inputs=[{"name": "pairs"}, {"name": "match_at"}],
            nodes=[
                {
                    "id": 1,
                    "type": "Score",
                    # Data the step takes may arrive through a widget too (a folder name).
                    "inputs": [
                        {"name": "pairs", "widget": {"name": "pairs"}},
                        {"name": "match_at", "widget": {"name": "match_at"}},
                    ],
                }
            ],
            links=[
                {"origin_id": -10, "origin_slot": 0, "target_id": 1, "target_slot": 0},
                {"origin_id": -10, "origin_slot": 1, "target_id": 1, "target_slot": 1},
            ],
        )
        nodes = infer_realizes(stored)["definitions"]["subgraphs"][0]["nodes"]
        self.assertEqual(nodes[0]["properties"]["aws_realizes"], ["takes pairs", "does"])


if __name__ == "__main__":
    unittest.main()
