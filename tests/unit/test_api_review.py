"""The API design standard: each rule names what to fix, compatibility decides the version, and
the generated OpenAPI describes a data API completely (native upload, typed tables, problem errors).

Verifies: R16, R38
"""

import copy
import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.api_review import (
    compatibility,
    contract_rules,
    design_rules,
    openapi_lint,
)
from custom_nodes.agent_workflow_studio.native_layers import embed
from custom_nodes.agent_workflow_studio.workflow_api import missing_data, next_version, openapi

from .test_native_layers import workflow
from .test_workflow_contract import CONTRACT


def failing(rules: list[tuple[bool, str]]) -> list[str]:
    return [item for ok, item in rules if not ok]


def data_contract() -> dict:
    """A table in, a typed table out: the claim-check shape a data API should have."""
    contract = copy.deepcopy(CONTRACT)
    contract["promises"]["inputs"] = [
        {"name": "orders", "kind": "folder", "description": "The order exports, uploaded."}
    ]
    contract["promises"]["outputs"] = [
        {
            "name": "totals",
            "kind": "table",
            "description": "Revenue per region.",
            "from": "tidy.label",
        }
    ]
    contract["examples"][0].update(given={"orders": "orders_2026"}, expect={"totals": "rows 4"})
    contract["steps"][0]["takes"] = {"review": "input.orders"}
    contract["data"] = {
        "totals": {
            "description": "One row per region",
            "key": ["region"],
            "fields": ["region", "revenue"],
        }
    }
    return contract


SIGNATURE = {
    "inputs": [{"name": "orders", "node": 6, "widget": "value", "type": "STRING", "default": ""}],
    "outputs": [{"name": "totals", "node": 7}],
}


class ApiReviewTests(unittest.TestCase):
    def test_a_good_data_interface_passes_every_rule(self) -> None:
        contract = data_contract()
        self.assertEqual(failing(contract_rules(contract) + design_rules(contract)), [])

    def test_each_rule_names_what_to_fix(self) -> None:
        def broken(change):
            contract = data_contract()
            change(contract)
            return " | ".join(failing(contract_rules(contract) + design_rules(contract)))

        cases = {
            "rename: value": lambda c: c["promises"]["inputs"][0].update(name="value"),
            "describe: totals": lambda c: c["promises"]["outputs"][0].pop("description"),
            "change: orders": lambda c: c["promises"]["inputs"][0].update(kind="csv"),
            "remove: api_key": lambda c: c["promises"]["inputs"].append(
                {"name": "api_key", "kind": "text", "description": "x"}
            ),
            "use in an example: output totals": lambda c: c["examples"][0].update(expect={}),
            "add fields for: totals": lambda c: c["data"].pop("totals"),
            "read: orders": lambda c: c["steps"][0].update(takes={"review": "STRING"}),
        }
        for fix, change in cases.items():
            with self.subTest(fix):
                self.assertIn(fix, broken(change))

    def test_compatibility_decides_the_version(self) -> None:
        old = {"semver": "1.2.3", "signature": SIGNATURE, "design": "d"}

        def changed(change):
            new = copy.deepcopy(SIGNATURE)
            change(new)
            return new

        removed = changed(lambda s: s["inputs"].clear())
        retyped = changed(lambda s: s["inputs"][0].update(type="INT"))
        required = changed(lambda s: s["inputs"].append({"name": "region", "type": "STRING"}))
        optional = changed(
            lambda s: s["inputs"].append({"name": "region", "type": "STRING", "default": "all"})
        )
        more_out = changed(lambda s: s["outputs"].append({"name": "chart", "node": 8}))
        self.assertEqual(compatibility(SIGNATURE, removed)["breaking"], ["input orders removed"])
        self.assertEqual(next_version(old, removed, "d"), "2.0.0")
        self.assertEqual(next_version(old, retyped, "d"), "2.0.0")
        self.assertEqual(next_version(old, required, "d"), "2.0.0")
        self.assertEqual(next_version(old, optional, "d"), "1.3.0")
        self.assertEqual(next_version(old, more_out, "d"), "1.3.0")
        self.assertEqual(next_version(old, SIGNATURE, "d"), "1.2.4")
        self.assertEqual(next_version(old, SIGNATURE, "other design"), "1.3.0")
        from custom_nodes.agent_workflow_studio.workflow_api import release_notes

        renamed = changed(lambda s: s["inputs"][0].update(name="exports"))
        self.assertEqual(
            release_notes(old, renamed, "d"),
            "Major: breaking for callers — input orders removed. Added: input exports added.",
        )
        # A release from before version numbers counts as 1.0.0.
        self.assertEqual(next_version({"signature": SIGNATURE}, removed, "d"), "2.0.0")

    def test_the_generated_openapi_describes_a_data_api_completely(self) -> None:
        stored = embed(data_contract(), workflow())
        spec = {
            "name": "sales",
            "signature": SIGNATURE,
            "workflow": stored,
            "version": "abc",
            "semver": "1.0.0",
        }
        doc = openapi(spec)
        self.assertEqual(openapi_lint(doc), [])
        run = doc["paths"]["/agent-workflow-studio/api/sales/run"]["post"]
        body = run["requestBody"]["content"]["application/json"]
        orders = body["schema"]["properties"]["inputs"]["properties"]["orders"]
        self.assertEqual(orders["format"], "comfyui-input-folder")
        self.assertIn("upload it first", orders["description"])
        self.assertEqual(body["examples"]["E1"]["value"], {"inputs": {"orders": "orders_2026"}})
        totals = run["responses"]["200"]["content"]["application/json"]["schema"]["properties"][
            "data"
        ]
        self.assertEqual(
            totals["properties"]["totals"]["properties"]["columns"]["items"]["enum"],
            ["region", "revenue"],
        )
        self.assertIn("application/problem+json", run["responses"]["400"]["content"])
        # The native pieces a caller uses: upload first (claim check), jobs to poll and cancel.
        self.assertIn("/upload/image", doc["paths"])
        self.assertIn("/api/jobs/{job}/cancel", doc["paths"])
        # A description without what callers need is caught.
        del orders["description"]
        self.assertIn("input orders: no description", openapi_lint(doc))

    def test_data_must_be_uploaded_before_the_call(self) -> None:
        stored = embed(data_contract(), workflow())
        spec = {"signature": SIGNATURE, "workflow": stored}
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "orders_2026").mkdir()
            self.assertEqual(missing_data(spec, {"orders": "orders_2026"}, Path(root)), [])
            for value in ("nope", "../outside", ""):
                with self.subTest(value):
                    [problem] = missing_data(spec, {"orders": value}, Path(root))
                    self.assertIn("upload it first with POST /upload/image", problem)


if __name__ == "__main__":
    unittest.main()
