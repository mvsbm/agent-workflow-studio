"""Workflow API: what a publish needs, typed inputs into the frozen prompt, outputs by stage, OpenAPI."""

import unittest

from custom_nodes.agent_workflow_studio.workflow_api import (
    bind_inputs,
    check_spec,
    collect_checks,
    collect_data,
    collect_files,
    collect_outputs,
    compare_example,
    next_version,
    openapi,
)

SPEC = {
    "name": "greet",
    "version": "abc1234567",
    # A given text node at the top level (id 3) feeding stage 5, whose nodes run as "5:…".
    "prompt": {
        "3": {"class_type": "PrimitiveString", "inputs": {"value": "hello"}},
        "5:1": {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": ["3", 0], "string_b": " world"},
        },
        "5:2": {"class_type": "PreviewAny", "inputs": {"source": ["5:1", 0]}},
    },
    "signature": {
        "inputs": [
            {
                "name": "text",
                "node": 3,
                "widget": "value",
                "type": "STRING",
                "default": "hello",
                "title": "Greeting",
            }
        ],
        "outputs": [{"name": "greeting", "node": 5, "title": "1 · Greet"}],
    },
}


class WorkflowApiTests(unittest.TestCase):
    def test_a_publish_needs_a_name_outputs_and_reachable_inputs(self) -> None:
        self.assertIs(check_spec(SPEC), SPEC)
        for change, message in (
            ({"name": "Bad Name"}, "lowercase"),
            ({"signature": {"inputs": [], "outputs": []}}, "outputs"),
            (
                {
                    "signature": {
                        "inputs": [{"name": "x", "node": 5, "widget": "value"}],
                        "outputs": SPEC["signature"]["outputs"],
                    }
                },
                "top-level node",
            ),
            ({"signature": {"inputs": [], "outputs": [{"name": "y", "node": 9}]}}, "not a stage"),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                check_spec({**SPEC, **change})

    def test_inputs_are_typed_and_land_in_the_frozen_prompt(self) -> None:
        prompt = bind_inputs(SPEC, {"text": "hi"})
        self.assertEqual(prompt["3"]["inputs"]["value"], "hi")
        self.assertEqual(
            SPEC["prompt"]["3"]["inputs"]["value"], "hello", "the published prompt is not changed"
        )
        with self.assertRaisesRegex(ValueError, "must be string"):
            bind_inputs(SPEC, {"text": 3})
        with self.assertRaisesRegex(ValueError, "Unknown input"):
            bind_inputs(SPEC, {"other": "x"})
        # A widget promoted on a subgraph card sets every node inside that it feeds.
        shared = {
            **SPEC,
            "signature": {
                **SPEC["signature"],
                "inputs": [
                    {
                        **SPEC["signature"]["inputs"][0],
                        "targets": [["3", "value"], ["5:1", "string_b"]],
                    }
                ],
            },
        }
        prompt = bind_inputs(check_spec(shared), {"text": "x"})
        self.assertEqual(
            (prompt["3"]["inputs"]["value"], prompt["5:1"]["inputs"]["string_b"]), ("x", "x")
        )

    def test_outputs_are_the_results_inside_each_output_stage(self) -> None:
        outputs = {
            "5:2": {"text": ["hello world"]},
            "3": {"text": ["ignored"]},
            "50:1": {"text": ["not stage 5"]},
        }
        self.assertEqual(
            collect_outputs(SPEC, outputs), {"greeting": {"5:2": {"text": ["hello world"]}}}
        )

    def test_examples_compare_against_the_promised_outputs(self) -> None:
        outputs = {
            "5:9": {"aws_output": [{"name": "greeting", "value": "Hello World"}]},
            "5:2": {"text": ["rows 3"]},
        }
        data = collect_data(outputs)
        self.assertEqual(data, {"greeting": "Hello World"})
        self.assertEqual(compare_example({"greeting": "hello world"}, data, outputs), [])
        self.assertEqual(
            compare_example({"rows": "rows 3"}, data, outputs), [], "found anywhere in the results"
        )
        self.assertEqual(len(compare_example({"greeting": "goodbye"}, data, outputs)), 1)

    def test_versions_say_what_changed(self) -> None:
        sig = SPEC["signature"]
        first = {"semver": "1.0.0", "signature": sig, "design": "d1"}
        self.assertEqual(next_version(None, sig, "d1"), "1.0.0")
        self.assertEqual(next_version(first, sig, "d1"), "1.0.1", "inside a stage: patch")
        self.assertEqual(next_version(first, sig, "d2"), "1.1.0", "the design: minor")
        renamed = {**sig, "inputs": [{**sig["inputs"][0], "name": "greeting"}]}
        self.assertEqual(next_version(first, renamed, "d1"), "2.0.0", "what callers send: major")
        # Each release says why its number moved.
        from custom_nodes.agent_workflow_studio.workflow_api import release_notes

        self.assertTrue(release_notes(first, renamed, "d1").startswith("Major: the API changed"))
        self.assertTrue(release_notes(first, sig, "d2").startswith("Minor: the design changed"))
        self.assertTrue(release_notes(first, sig, "d1").startswith("Patch: only inside stages"))
        self.assertEqual(release_notes(None, sig, "d1"), "First release.")

    def test_every_check_that_ran_is_collected(self) -> None:
        outputs = {
            "3:7": {"aws_check": [{"passed": True, "evidence": "5 row(s)"}]},
            "4:2": {"aws_check": [{"passed": False, "evidence": "0 row(s)"}]},
            "9": {"text": ["x"]},
        }
        self.assertEqual(
            [(c["node"], c["passed"]) for c in collect_checks(outputs)],
            [("3:7", True), ("4:2", False)],
        )

    def test_written_files_become_download_links(self) -> None:
        files = collect_files({"7": {"aws_files": ["reports/out.csv"]}})
        self.assertEqual(
            files,
            [
                {
                    "path": "output/reports/out.csv",
                    "url": "/view?filename=out.csv&subfolder=reports&type=output",
                }
            ],
        )

    def test_openapi_describes_the_call(self) -> None:
        doc = openapi(SPEC)
        body = doc["paths"]["/agent-workflow-studio/api/greet/run"]["post"]["requestBody"]
        text = body["content"]["application/json"]["schema"]["properties"]["inputs"]["properties"][
            "text"
        ]
        self.assertEqual((text["type"], text["default"]), ("string", "hello"))


if __name__ == "__main__":
    unittest.main()
