"""Plan vs built vs proven: the gaps are named from evidence, never from claims."""

# Verifies: R27, R28

import copy
import unittest

from custom_nodes.agent_workflow_studio.workflow_api import prompt_hash
from custom_nodes.agent_workflow_studio.workflow_contract import report

from .test_workflow_contract import CONTRACT

PROMPT = {"1": {"class_type": "PreviewAny", "inputs": {}}}


def evidence(checks, gates, built=True, prompt=PROMPT):
    nodes = [
        {"title": "Label it", "class_type": "AgentWorkflowPiStructuredAgent", "step": "classify"},
        {"title": "Plain words", "class_type": "RegexReplace", "step": "tidy"},
    ]
    return {
        "nodes": nodes if built else [],
        "stages": [
            {
                "step": "classify",
                "title": "1 · Classify",
                "takes": [],
                "gives": [],
                "checks": checks,
            },
            {"step": "tidy", "title": "2 · Tidy", "takes": [], "gives": [], "checks": []},
        ],
        "gate_results": gates,
        "gate_checks": {},
        "run": {"current": True},
        "prompt": prompt,
    }


def gaps(result):
    return [f"{g['severity']} · {g['where']}: {g['text']}" for g in result["trace"]["gaps"]]


class ProgressTests(unittest.TestCase):
    def test_a_check_weaker_than_agreed_is_named_even_when_it_passes(self) -> None:
        weak = [{"watches": "label", "kind": "non_empty", "expect": ""}]
        result = report(CONTRACT, evidence(weak, {"classify": "passed", "tidy": "passed"}))
        found = gaps(result)
        self.assertIn(
            "unproven · step classify: its Check differs from the one agreed: agreed regex "
            "positive|negative on label, built non_empty on label",
            found,
        )
        self.assertIn(
            "unproven · step classify: verified only by a Check that data exists, not that it is right",
            found,
        )
        self.assertIn("1 of them only shallowly", result["trace"]["summary"])

    def test_requests_are_proven_only_by_examples_passing_on_this_version(self) -> None:
        agreed = [{"watches": "label", "kind": "regex", "expect": "positive|negative"}]
        gates = {"classify": "passed", "tidy": "passed"}
        passed = {
            "prompt_hash": prompt_hash(PROMPT),
            "results": [{"example": "E1", "passed": True, "misses": []}],
        }
        r1 = lambda result: result["trace"]["requests"][0]  # noqa: E731
        self.assertEqual(r1(report(CONTRACT, evidence(agreed, gates), passed))["verdict"], "proven")
        older = {**passed, "prompt_hash": "something else"}
        result = report(CONTRACT, evidence(agreed, gates), older)
        self.assertEqual(r1(result)["verdict"], "proven earlier")
        self.assertIn("unproven · request R1: examples have not run on this version", gaps(result))
        failed = {
            **passed,
            "results": [{"example": "E1", "passed": False, "misses": ["label: got neutral"]}],
        }
        result = report(CONTRACT, evidence(agreed, gates), failed)
        self.assertEqual(r1(result)["verdict"], "failing")
        self.assertEqual(result["trace"]["gaps"][0]["severity"], "blocking")  # most serious first
        result = report(CONTRACT, evidence(agreed, gates))
        self.assertEqual(r1(result)["verdict"], "unproven")

    def test_design_differences_and_deferred_scope_are_shown(self) -> None:
        contract = copy.deepcopy(CONTRACT)
        contract["requests"].append(
            {"id": "R3", "you_said": "x", "we_understood": "When x, y.", "priority": "should"}
        )
        found = gaps(report(contract, evidence([], {"classify": "failed"})))
        self.assertIn("design · requests R2, R3: no step says it covers this request", found)
        self.assertIn(
            "design · steps classify, tidy: its nodes do not say which part of the design they realize",
            found,
        )
        self.assertIn("deferred · agreed not now: Languages other than English", found)
        self.assertIn("blocking · step classify: its Check fails on the latest run", found)
        self.assertIn(
            "unproven · step classify: the agreed Check (regex positive|negative on label) is missing",
            found,
        )

    def test_the_lld_traces_each_hld_clause_to_the_nodes_that_realize_it(self) -> None:
        seen = evidence([], {})
        seen["nodes"][0]["realizes"] = ["takes review", "does"]  # Label it
        seen["nodes"].append(
            {"title": "Debug print", "class_type": "PreviewAny", "step": "classify"}
        )
        result = report(CONTRACT, seen)
        rows = {r["clause"]: r["by"] for r in result["trace"]["steps"][0]["realization"]["rows"]}
        self.assertEqual(rows["does"], ["Label it"])
        self.assertEqual(rows["gives label"], [])
        found = gaps(result)
        self.assertIn("design · step classify: no node realizes: gives label", found)
        self.assertIn(
            "design · step classify: + extra, in no part of the design: Debug print", found
        )
        seen["nodes"][0]["realizes"] = ["takes review", "does", "gives label"]
        seen["nodes"].pop()
        self.assertFalse(
            [g for g in gaps(report(CONTRACT, seen)) if "step classify:" in g and "design" in g]
        )

    def test_a_request_without_example_is_checked_by_its_steps_at_most(self) -> None:
        agreed = [{"watches": "label", "kind": "regex", "expect": "positive|negative"}]
        contract = copy.deepcopy(CONTRACT)
        contract["examples"] = []
        result = report(contract, evidence(agreed, {"classify": "passed", "tidy": "passed"}))
        self.assertEqual(result["trace"]["requests"][0]["verdict"], "checked by its steps")
        self.assertIn(
            "unproven · request R1: a must-have with no example to prove it", gaps(result)
        )

    def test_nothing_built_is_not_progress(self) -> None:
        result = report(CONTRACT, evidence([], {}, built=False))
        self.assertIn("unproven · steps classify, tidy: not built yet", gaps(result))  # one line
        self.assertIn("steps verified 0/2", result["trace"]["summary"])

    def test_app_mode_is_the_contracts_interface(self) -> None:
        from custom_nodes.agent_workflow_studio.progress import app_gaps

        from .test_native_layers import workflow

        app = workflow()
        app["nodes"].append(
            {"id": 7, "title": "Input · review", "properties": {"aws_input": "review"}}
        )
        app["nodes"].append(
            {"id": 8, "type": "AgentWorkflowApiOutput", "properties": {"aws_output": "label"}}
        )
        app["extra"]["linearData"] = {"inputs": [[7, "value"]], "outputs": ["8"]}
        self.assertEqual(
            app_gaps(CONTRACT, app), []
        )  # asks what is promised, shows what is promised
        app["extra"]["linearData"] = {"inputs": [[7, "value"], [5, "preview"]], "outputs": ["4"]}
        self.assertEqual(
            app_gaps(CONTRACT, app),
            [
                "App mode asks for 5 · preview, which the contract does not promise",
                "output label is promised, but App mode does not show it",
            ],
        )
        app["extra"]["linearData"] = {"inputs": [], "outputs": []}
        self.assertIn(
            "input review is promised, but App mode does not ask for it", app_gaps(CONTRACT, app)
        )


if __name__ == "__main__":
    unittest.main()
