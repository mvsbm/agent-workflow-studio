import re
import unittest
from pathlib import Path

import yaml


class WorkflowPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/agent-runtime-spike.yml"
        )
        self.source = path.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=yaml.BaseLoader)

    def test_read_only_token_and_no_secret_references(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotRegex(self.source, r"secrets\s*[.\[]")
        self.assertEqual(
            set(self.workflow["on"]), {"push", "pull_request", "workflow_dispatch"}
        )

    def test_ephemeral_runner_timeout_and_immutable_actions(self) -> None:
        job = self.workflow["jobs"]["validate"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["timeout-minutes"], "10")
        for step in job["steps"]:
            if "uses" in step:
                self.assertRegex(
                    step["uses"], r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+@[a-f0-9]{40}$"
                )
        checkout = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["persist-credentials"], "false")

    def test_delivery_is_explicit_evidence_only(self) -> None:
        job = self.workflow["jobs"]["validate"]
        delivery = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", delivery["if"])
        self.assertIn("inputs.publish_evidence", delivery["if"])
        self.assertIn("success()", delivery["if"])
        self.assertEqual(delivery["with"]["retention-days"], "7")
        self.assertEqual(
            self.workflow["on"]["workflow_dispatch"]["inputs"]["publish_evidence"][
                "default"
            ],
            "false",
        )
        self.assertIn("synthetic", delivery["with"]["name"])

    def test_hash_locked_install_and_explicit_fixture_gate(self) -> None:
        job = self.workflow["jobs"]["validate"]
        self.assertEqual(job["env"]["COMFYUI_LANGGRAPH_SPIKE_ALLOWED"], "1")
        install = next(
            step["run"]
            for step in job["steps"]
            if "run" in step and "uv pip install" in step["run"]
        )
        self.assertIn("--require-hashes", install)
        self.assertIn("--only-binary :all:", install)
        self.assertIn("--default-index https://pypi.org/simple", install)
        self.assertIn("ci/run_langgraph_spike.py", self.source)
        self.assertNotIn("pull_request_target", self.source)
        self.assertFalse(re.search(r"permissions:\s*write-all", self.source))
