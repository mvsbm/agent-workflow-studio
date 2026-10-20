"""The workflow contract: schema rules, YAML, conversion of old plans, lifecycle, preview."""

# Verifies: R10, R27, R29, R31

from __future__ import annotations

import copy
import json
import unittest

from custom_nodes.agent_workflow_studio.workflow_contract import (
    SCHEMA_PATH,
    TRANSITIONS,
    blocking,
    derive_states,
    render,
    review,
    validate_contract,
    validate_for_approval,
)

CONTRACT = {
    "contract": 2,
    "purpose": "Label customer reviews so support can triage them.",
    "requests": [
        {
            "id": "R1",
            "title": "Label each review",
            "you_said": "tell me if reviews are good or bad",
            "we_understood": "When a review arrives, the workflow labels it positive or negative.",
            "priority": "must",
        },
        {
            "id": "R2",
            "title": "Plain words, no emoji",
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
        "inputs": [
            {
                "name": "review",
                "kind": "text",
                "description": "One customer review, as written.",
                "example": "Great value",
            }
        ],
        "outputs": [
            {
                "name": "label",
                "kind": "text",
                "description": "positive or negative.",
                "from": "tidy.label",
            }
        ],
    },
    "steps": [
        {
            "id": "classify",
            "purpose": "Decide whether the review is positive or negative.",
            "approach": "ai_judgment",
            "covers": ["R1"],
            "takes": {"review": "input.review"},
            "pattern": "LLM behind a typed boundary",
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
            "pattern": "message translator",
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
        self.assertEqual(validate_for_approval({**CONTRACT, "contract": "2"}), CONTRACT)

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

    def test_the_agents_schemas_are_derived_from_the_contract_schema(self) -> None:
        from custom_nodes.agent_workflow_studio.workflow_contract import SCHEMA_PATH, agent_schemas

        for name, schema in agent_schemas().items():
            with self.subTest(name):
                saved = json.loads(SCHEMA_PATH.with_name(f"{name}.schema.json").read_text())
                self.assertEqual(
                    saved, schema, "run write_agent_schemas() after changing the contract schema"
                )

    def test_the_contract_is_intent_and_the_design_joins_it(self) -> None:
        from custom_nodes.agent_workflow_studio.workflow_contract import join, validate_intent

        intent = {k: copy.deepcopy(v) for k, v in CONTRACT.items() if k != "steps"}
        source = intent["promises"]["outputs"][0].pop("from")
        self.assertIs(validate_intent(intent), intent)
        with self.assertRaisesRegex(ValueError, "steps belong to the design"):
            validate_intent({**intent, "steps": CONTRACT["steps"]})
        with self.assertRaisesRegex(ValueError, "Which step delivers a promised output is design"):
            validate_intent(changed(lambda c: c.pop("steps")))
        joined = join(intent, {"steps": CONTRACT["steps"], "outputs": {"label": source}})
        self.assertEqual(validate_contract(joined), CONTRACT)

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
        # A verified review step with open items: the user's turn, not a problem.
        review = derive_states(CONTRACT, [{"step": "tidy"}], {"tidy": "passed"}, {"tidy": 1606})[1]
        self.assertEqual((review["state"], review["next"][:16]), ("verified", "Your turn: 1,606"))
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

    def test_a_duplicate_id_is_named(self) -> None:
        twice = copy.deepcopy(CONTRACT)
        twice["decisions"].append({**twice["decisions"][0], "by": "agent"})
        with self.assertRaisesRegex(ValueError, "D1 used twice .*share one numbering"):
            validate_contract(twice)

    def test_a_request_is_named_by_its_title_never_its_id_alone(self) -> None:
        from custom_nodes.agent_workflow_studio.native_layers import intent_of
        from custom_nodes.agent_workflow_studio.workflow_contract import title_of, validate_intent

        untitled = intent_of(copy.deepcopy(CONTRACT))
        untitled["requests"][1].pop("title")
        with self.assertRaisesRegex(ValueError, "short title.*R2"):
            validate_intent(untitled)
        # A contract stored before titles existed gets one from its restatement.
        self.assertEqual(
            title_of({"we_understood": "When records are cleaned, each system gets its own lane."}),
            "Each system gets its own lane",
        )

    def test_plain_preview(self) -> None:
        text = render(CONTRACT, [{"step": "classify", "state": "verified"}])
        self.assertTrue(text.startswith("PURPOSE: Label customer reviews"))
        self.assertIn(
            "R1 · Label each review (must) you said: “tell me if reviews are good or bad”", text
        )
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
        from custom_nodes.agent_workflow_studio.workflow_contract import report

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
        from custom_nodes.agent_workflow_studio.workflow_contract import alignment

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

    def test_a_person_step_and_a_given_input_need_no_wiring_or_check(self) -> None:
        contract = copy.deepcopy(CONTRACT)
        contract["steps"][1]["gate"] = {"says": "You confirm each label."}
        contract["promises"]["inputs"] = [
            {"name": "seed", "kind": "integer", "node": 9, "widget": "seed"}
        ]
        contract["steps"][0]["takes"] = {"review": "STRING"}
        checked = "Every step's result is checked on its data"
        self.assertFalse(dict((i, ok) for ok, i in review(contract))[checked])  # nobody checks tidy
        contract["steps"][1]["nodes"] = ["AgentWorkflowReviewDecisions"]  # a person does
        items = {i: ok for ok, i in review(contract)}
        self.assertTrue(items[checked])
        self.assertTrue(
            items["The API is defined: every input is used, every output comes from a step"]
        )

    def test_the_design_reads_as_sentences_and_is_reviewed(self) -> None:
        from custom_nodes.agent_workflow_studio.workflow_contract import hld, review

        design = changed(
            lambda c: (
                c["promises"].update(
                    inputs=[{"name": "review", "kind": "text", "description": "One review."}],
                    outputs=[
                        {
                            "name": "label",
                            "kind": "text",
                            "description": "Its label.",
                            "from": "tidy.label",
                        }
                    ],
                ),
                c["steps"][0].update(
                    takes={"review": "input.review"},
                    uses="an LLM with a fixed schema",
                    why="tone needs judgment",
                    pattern="LLM behind a typed boundary",
                ),
                c["steps"][1].update(
                    uses="Map values",
                    why="support reads plain labels",
                    pattern="message translator",
                    gate={
                        "says": "plain",
                        "check": {
                            "output": "label",
                            "kind": "regex",
                            "value": "^(positive|negative)$",
                        },
                    },
                ),
                c.update(data={"label": {"description": "positive or negative, per review"}}),
            )
        )
        validate_contract(design)
        sentences = [d["sentence"] for d in hld(design)]
        self.assertEqual(
            sentences[0],
            "takes review (your input) → Decide whether the review is positive or negative using an LLM "
            "with a fixed schema → gives label (STRING) → to 2 · Tidy, because tone needs judgment",
        )
        self.assertIn("→ to you (output label)", sentences[1])
        self.assertTrue(all(ok for ok, _ in review(design)), review(design))
        # A check that only shows the data exists cannot verify a step: the proposal is refused.
        exists = copy.deepcopy(design)
        exists["steps"][1]["gate"]["check"] = {"output": "label", "kind": "non_empty"}
        self.assertEqual(
            [b.split(" — ")[1] for b in blocking(exists)], ["only checks existence in: tidy"]
        )
        bare = {item: ok for ok, item in review(CONTRACT)}
        self.assertFalse(bare["Every step says how and why"])
        self.assertIn("DESIGN REVIEW:", render(design))
        for broken, message in (
            (lambda c: c["steps"][0].update(takes={"x": "input.nope"}), "not a promised input"),
            (
                lambda c: c["promises"]["outputs"][0].update({"from": "tidy.nothing"}),
                "no step gives",
            ),
            (
                lambda c: c.update(data={"a": {"description": "x", "refs": {"b_id": "b"}}}),
                "unknown entity",
            ),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                validate_contract(changed(broken))

    def test_declared_fields_the_run_did_not_carry_are_drift(self) -> None:
        from custom_nodes.agent_workflow_studio.workflow_contract import alignment

        design = changed(
            lambda c: c.update(data={"label": {"description": "x", "fields": ["text", "score"]}})
        )
        stage = {
            "step": "classify",
            "title": "1 · Classify",
            "takes": [["review", "STRING"]],
            "gives": [["label", "STRING", ["text"]]],
        }
        self.assertEqual(
            alignment(design, {"nodes": [], "stages": [stage]}),
            ["1 · Classify gives label without score (declared in the design)."],
        )

    def test_the_design_standard_blocks_inconsistent_proposals(self) -> None:
        from custom_nodes.agent_workflow_studio.workflow_contract import blocking

        self.assertEqual(blocking(CONTRACT), [])
        unmapped = changed(lambda c: c["promises"]["outputs"][0].pop("from"))
        self.assertEqual(len(blocking(unmapped)), 1)
        self.assertTrue(blocking(unmapped)[0].startswith("The API is defined"))
        wordy = changed(lambda c: c["steps"][0].update(why="x" * 200))
        self.assertTrue(
            any(
                item.startswith("Every step reads as one short sentence")
                for item in blocking(wordy)
            )
        )

    def test_schema_file_is_valid_and_shared(self) -> None:
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text()))
        self.assertIn(
            '"./contract.schema.json"', (SCHEMA_PATH.parent / "pi_chat_extension.ts").read_text()
        )


if __name__ == "__main__":
    unittest.main()
