"""The workflow contract: schema rules, YAML, conversion of old plans, lifecycle, preview."""

from __future__ import annotations

import copy
import json
import unittest

from .workflow_contract import (
    SCHEMA_PATH,
    TRANSITIONS,
    derive_states,
    from_plan_v1,
    from_yaml,
    render,
    to_yaml,
    validate_contract,
    validate_for_approval,
)

CONTRACT = {
    "contract": 2,
    "purpose": "Label customer reviews so support can triage them.",
    "requests": [
        {
            "id": "R1",
            "you_said": "tell me if reviews are good or bad",
            "we_understood": "When a review arrives, the workflow labels it positive or negative.",
            "priority": "must",
        },
        {
            "id": "R2",
            "you_said": "no emoji please",
            "we_understood": "When labelling, the workflow outputs plain words.",
            "priority": "could",
        },
    ],
    "examples": [
        {
            "id": "E1",
            "covers": ["R1"],
            "given": {"review": "I love it"},
            "expect": {"label": "positive"},
        }
    ],
    "promises": {
        "inputs": [{"name": "review", "kind": "text", "example": "Great value"}],
        "outputs": [{"name": "label", "kind": "text"}],
    },
    "steps": [
        {
            "id": "classify",
            "purpose": "Decide whether the review is positive or negative.",
            "approach": "ai_judgment",
            "covers": ["R1"],
            "takes": {"review": "STRING"},
            "gives": {"label": "STRING"},
            "gate": {
                "says": "The label is positive or negative.",
                "check": {"output": "label", "kind": "regex", "value": "positive|negative"},
            },
            "nodes": ["AgentWorkflowPiStructuredAgent", "PreviewAny"],
        },
        {
            "id": "tidy",
            "purpose": "Keep the label plain.",
            "approach": "fixed",
            "covers": ["R1"],
            "takes": {"label": "classify.label"},
            "gives": {"label": "STRING"},
            "gate": {"says": "No emoji in the label."},
        },
    ],
    "decisions": [
        {
            "id": "D1",
            "decision": "Mixed reviews count as negative.",
            "why": "Support wants every complaint.",
            "instead_of": "A neutral label",
            "by": "user",
        }
    ],
    "not_now": ["Languages other than English"],
    "open_questions": [],
}


def changed(change):
    contract = copy.deepcopy(CONTRACT)
    change(contract)
    return contract


class ContractTests(unittest.TestCase):
    def test_valid_contract_and_approval(self) -> None:
        self.assertIs(validate_for_approval(CONTRACT), CONTRACT)

    def test_rejects_inconsistent_contracts(self) -> None:
        cases = {
            "unknown request": changed(lambda c: c["steps"][0]["covers"].append("R9")),
            "No step covers request": changed(
                lambda c: c["requests"].append({**c["requests"][0], "id": "R3"})
            ),
            "unique id": changed(lambda c: c["steps"][1].update(id="classify")),
            "needs a value": changed(lambda c: c["steps"][0]["gate"]["check"].pop("value")),
            "approach": changed(lambda c: c["steps"][0].update(approach="magic")),
            "Additional properties": changed(lambda c: c.update(status="done")),
            "you_said": changed(lambda c: c["requests"][0].pop("you_said")),
            "does not give": changed(
                lambda c: c["steps"][0]["gate"]["check"].update(output="score")
            ),
            "no earlier step gives": changed(
                lambda c: c["steps"][1]["takes"].update(label="tidy.label")
            ),
            "does not match": changed(lambda c: c["steps"][0]["gives"].update(label="string")),
        }
        for message, contract in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                validate_contract(contract)
        # A could-priority request may stay uncovered; open questions block only approval.
        validate_contract(changed(lambda c: c["steps"][1].update(covers=["R1"])))
        with self.assertRaisesRegex(ValueError, "open questions"):
            validate_for_approval(changed(lambda c: c["open_questions"].append("Which language?")))
        # A question tied to the one step that waits for it does not block approval.
        tied = changed(
            lambda c: c["open_questions"].append({"question": "Emoji allowed?", "step": "tidy"})
        )
        self.assertIs(validate_for_approval(tied), tied)
        with self.assertRaisesRegex(ValueError, "unknown step"):
            validate_contract(
                changed(lambda c: c["open_questions"].append({"question": "?", "step": "nope"}))
            )

    def test_yaml_round_trip_keeps_strings_as_strings(self) -> None:
        tricky = changed(lambda c: c["examples"][0]["expect"].update(label="no"))
        text = to_yaml(tricky)
        self.assertTrue(text.startswith("# yaml-language-server: $schema=contract.schema.json"))
        self.assertEqual(from_yaml(text), tricky)  # "no" stays a string, not false
        with self.assertRaises(ValueError):
            from_yaml("contract: 2\npurpose: x\n")

    def test_converts_plans_approved_before_contracts(self) -> None:
        plan = {
            "goal": "Show the text hello world.",
            "inputs": [],
            "outputs": [{"name": "text", "description": "The greeting", "type": "text"}],
            "steps": [
                {
                    "id": "greet",
                    "purpose": "Join the words.",
                    "approach": "deterministic",
                    "nodes": ["StringConcatenate"],
                    "success_gate": {
                        "description": "Says hello world.",
                        "check": {
                            "node_title": "Result",
                            "kind": "contains",
                            "value": "hello world",
                        },
                    },
                },
                {
                    "id": "judge",
                    "purpose": "Rate it.",
                    "approach": "llm",
                    "success_gate": {"description": "Reads well."},
                },
            ],
            "assumptions": ["English only."],
            "decisions": [{"question": "Which greeting?", "answer": "hello world"}],
            "open_questions": [],
        }
        contract = from_plan_v1(plan)
        self.assertEqual(contract["purpose"], "Show the text hello world.")
        self.assertEqual([s["approach"] for s in contract["steps"]], ["fixed", "ai_judgment"])
        self.assertEqual(
            contract["steps"][0]["gate"]["check"],
            {"node": "Result", "kind": "contains", "value": "hello world"},
        )
        self.assertEqual(
            [(d["decision"], d["by"]) for d in contract["decisions"]],
            [("hello world", "user"), ("English only.", "agent")],
        )

    def test_states_are_derived_from_evidence_through_the_table(self) -> None:
        def states(nodes, results=None):
            return {s["step"]: s["state"] for s in derive_states(CONTRACT, nodes, results or {})}

        self.assertEqual(states([]), {"classify": "planned", "tidy": "planned"})
        self.assertEqual(states([{"step": "classify"}]), {"classify": "built", "tidy": "planned"})
        # A step waiting on the user's answer can be built and fail, but not be verified.
        waits = changed(
            lambda c: c["open_questions"].append({"question": "Emoji allowed?", "step": "tidy"})
        )
        tidy = derive_states(waits, [{"step": "tidy"}], {"tidy": "passed"})[1]
        self.assertEqual((tidy["state"], tidy["waiting"]), ("built", ["Emoji allowed?"]))
        self.assertEqual(tidy["next"], "Waiting on your answer: Emoji allowed?")
        # The skeleton's Stub and Checks do not make a stage built.
        self.assertEqual(states([{"step": "classify", "scaffold": True}])["classify"], "planned")
        self.assertEqual(states([{"step": "classify", "fuzzy": True}])["classify"], "fuzzy")
        self.assertEqual(
            states(
                [{"step": "classify"}, {"step": "tidy"}], {"classify": "passed", "tidy": "failed"}
            ),
            {"classify": "verified", "tidy": "failing"},
        )
        # A gate outcome for a step with no nodes cannot make it verified.
        self.assertEqual(states([], {"classify": "passed"})["classify"], "planned")
        self.assertEqual(
            states([{"step": "classify"}], {"classify": "needs_user_review"})["classify"], "built"
        )
        self.assertTrue(
            all(target in TRANSITIONS for row in TRANSITIONS.values() for target in row.values())
        )

    def test_plain_preview(self) -> None:
        text = render(CONTRACT, [{"step": "classify", "state": "verified"}])
        self.assertTrue(text.startswith("PURPOSE: Label customer reviews"))
        self.assertIn("R1 (must) you said: “tell me if reviews are good or bad”", text)
        self.assertIn("covers R1 — verified", text)
        self.assertIn("No emoji in the label. (you confirm this one)", text)
        self.assertIn("Stage: takes label ← classify.label · gives label: STRING", text)
        self.assertIn("(a Check on label: regex positive|negative)", text)
        self.assertIn(
            "D1 Mixed reviews count as negative. — why: Support wants every complaint. (instead of A neutral label) · by you",
            text,
        )
        self.assertLess(text.index("NOT NOW"), text.index("Technical details"))

    def test_report_from_evidence(self) -> None:
        from .workflow_contract import report

        nodes = [{"title": "Label", "step": "classify"}, {"title": "Stray", "step": None}]
        fresh = report(
            CONTRACT,
            {"nodes": nodes, "gate_results": {"classify": "passed"}, "run": {"current": True}},
        )
        self.assertEqual(fresh["progress"], "1/2 steps verified")
        self.assertEqual(fresh["freshness"], "Based on the latest run of this exact graph.")
        self.assertEqual(fresh["nodes_outside_steps"], ["Stray"])
        stale = report(CONTRACT, {"nodes": nodes, "gate_results": {}, "run": {"current": False}})
        self.assertEqual(stale["progress"], "0/2 steps verified")
        self.assertIn("graph changed since the last run", stale["freshness"])
        self.assertEqual(
            report(CONTRACT, {"nodes": []})["freshness"], "No run of this workflow yet."
        )
        # What the architecture still asks for comes from the saved workflow.
        workflow = {
            "nodes": [{"id": 1, "type": "s", "properties": {"aws_stage": "classify"}}],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "s",
                        "name": "1 · Classify",
                        "nodes": [],
                        "inputs": [],
                        "outputs": [{"name": "label", "type": "*"}],
                    }
                ]
            },
        }
        todo = report(CONTRACT, {"nodes": [], "workflow": workflow})["todo"]
        self.assertTrue(any("label is untyped" in t for t in todo), todo)
        self.assertTrue(any("has no check" in t for t in todo), todo)
        self.assertTrue(any(t.endswith("(release)") for t in todo), todo)

    def test_alignment_reports_drift_not_progress(self) -> None:
        from .workflow_contract import alignment

        good = {"nodes": [{"title": "Label", "step": "classify"}], "analysis": {"problems": []}}
        self.assertEqual(alignment(CONTRACT, good), [], "an unbuilt step is progress, not drift")
        self.assertEqual(alignment(CONTRACT, {"nodes": []}), [])
        drift = alignment(
            CONTRACT,
            {
                "nodes": [
                    {"title": "Label", "step": "classify", "fuzzy": False},
                    {"title": "Cleaner", "step": "tidy", "fuzzy": True},
                    {"title": "Stray", "step": None},
                    {"title": "Synthetic data", "step": None, "given": True},
                    {"title": "How to use", "step": None, "note": True},
                ],
                "stages": [
                    {
                        "step": "classify",
                        "title": "1 · Classify",
                        "takes": [["review", "STRING"]],
                        "gives": [["label", "PAIRS"]],
                    },
                    {"step": "removed_step", "title": "3 · Removed step", "takes": [], "gives": []},
                ],
                "analysis": {"problems": ['Required input "vae" (VAE) is not connected.']},
            },
        )
        self.assertEqual(
            drift,
            [
                "Outside every stage: Stray.",
                "1 · Classify gives label as PAIRS; the contract says STRING.",
                "3 · Removed step is no longer a contract step.",
                "Step tidy is fixed in the contract but uses a Fuzzy node.",
                'Graph problem: Required input "vae" (VAE) is not connected.',
            ],
        )

    def test_schema_file_is_valid_and_shared(self) -> None:
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text()))
        self.assertIn(
            '"./contract.schema.json"', (SCHEMA_PATH.parent / "pi_chat_extension.ts").read_text()
        )


if __name__ == "__main__":
    unittest.main()
