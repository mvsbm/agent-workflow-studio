"""Architecture rules: each kind fires on the workflow shape it guards, and only there."""

import unittest

from .architecture import COMPLETENESS, RELEASE, STRUCTURAL, check


def stage(id_, name, inner, ports=("PAIRS",)):
    return {
        "id": id_,
        "name": name,
        "nodes": [{"id": 1, "type": t} for t in inner],
        "inputs": [{"name": "in", "type": ports[0]}],
        "outputs": [{"name": "out", "type": p} for p in ports],
    }


def workflow(nodes, subgraphs, linear=None):
    return {
        "nodes": nodes,
        "definitions": {"subgraphs": subgraphs},
        "extra": {"linearData": linear or {}},
    }


class ArchitectureTests(unittest.TestCase):
    def test_a_finished_workflow_breaks_no_rule(self) -> None:
        wf = workflow(
            [
                {"id": 1, "type": "s1", "properties": {"aws_stage": "match"}},
                {"id": 2, "type": "MarkdownNote"},
            ],
            [stage("s1", "1 · Match", ["Rules", "AgentWorkflowCheck"])],
            {"inputs": [[1, "x"]], "outputs": [1]},
        )
        self.assertEqual(check(wf), [])

    def test_unfinished_work_is_completeness_and_release_not_structure(self) -> None:
        wf = workflow(
            [{"id": 1, "type": "s1", "properties": {"aws_stage": "match"}}],
            [stage("s1", "1 · Match", ["AgentWorkflowAgentTool"], ports=("*",))],
        )
        kinds = {v["rule"]: v["kind"] for v in check(wf)}
        self.assertEqual(
            kinds,
            {
                "ports_typed": COMPLETENESS,
                "stage_checked": COMPLETENESS,
                "has_signature": RELEASE,
                "no_fuzzy_steps": RELEASE,
            },
        )

    def test_loose_nodes_and_unknown_stages_are_structural(self) -> None:
        wf = workflow(
            [
                {"id": 1, "type": "Rules", "title": "Loose rules"},
                {"id": 2, "type": "s1"},
                {
                    "id": 3,
                    "type": "Rules",
                    "properties": {"aws_given": True},
                },  # the user's: left alone
            ],
            [stage("s1", "Mystery", ["AgentWorkflowCheck"])],
        )
        structural = [(v["rule"], v["where"]) for v in check(wf) if v["kind"] == STRUCTURAL]
        self.assertEqual(
            structural, [("node_in_a_stage", "Loose rules"), ("stage_is_a_step", "Mystery")]
        )

    def test_checks_inside_nested_stages_count(self) -> None:
        wf = workflow(
            [{"id": 1, "type": "outer", "properties": {"aws_stage": "match"}}],
            [
                stage("outer", "1 · Match", ["inner"]),
                stage("inner", "Rules", ["AgentWorkflowCheck"]),
            ],
        )
        self.assertNotIn("stage_checked", [v["rule"] for v in check(wf)])


if __name__ == "__main__":
    unittest.main()
