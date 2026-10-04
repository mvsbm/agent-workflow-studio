import os
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_langgraph_spike import run


class SpikeRunnerPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report = Path(self.directory.name) / "report.json"

    def test_missing_approval_never_discovers_framework_tests(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("run_langgraph_spike.unittest.TestLoader") as loader,
        ):
            self.assertEqual(run(self.report), 2)
            loader.assert_not_called()
        self.assertFalse(self.report.exists())

    def test_missing_dependency_fails_closed(self) -> None:
        with (
            patch.dict(
                os.environ, {"COMFYUI_LANGGRAPH_SPIKE_ALLOWED": "1"}, clear=True
            ),
            patch("run_langgraph_spike.version", side_effect=PackageNotFoundError),
        ):
            self.assertEqual(run(self.report), 2)
        self.assertFalse(self.report.exists())

    def test_wrong_version_fails_closed(self) -> None:
        with (
            patch.dict(
                os.environ, {"COMFYUI_LANGGRAPH_SPIKE_ALLOWED": "1"}, clear=True
            ),
            patch("run_langgraph_spike.version", return_value="0.0.0"),
        ):
            self.assertEqual(run(self.report), 2)
        self.assertFalse(self.report.exists())

    def test_skipped_fixture_is_not_a_green_gate(self) -> None:
        result = SimpleNamespace(
            wasSuccessful=lambda: True,
            testsRun=5,
            skipped=[("fixture", "unapproved")],
            failures=[],
            errors=[],
        )
        with (
            patch.dict(
                os.environ, {"COMFYUI_LANGGRAPH_SPIKE_ALLOWED": "1"}, clear=True
            ),
            patch("run_langgraph_spike.version", return_value="1.2.12"),
            patch("run_langgraph_spike.unittest.TextTestRunner") as runner,
        ):
            runner.return_value.run.return_value = result
            self.assertEqual(run(self.report), 1)
        self.assertTrue(self.report.exists())

    def test_wrong_fixture_names_are_not_a_green_gate(self) -> None:
        result = SimpleNamespace(
            wasSuccessful=lambda: True, testsRun=5, skipped=[], failures=[], errors=[]
        )
        suite = unittest.TestSuite(
            [unittest.FunctionTestCase(lambda: None) for _ in range(5)]
        )
        with (
            patch.dict(
                os.environ, {"COMFYUI_LANGGRAPH_SPIKE_ALLOWED": "1"}, clear=True
            ),
            patch("run_langgraph_spike.version", return_value="1.2.12"),
            patch("run_langgraph_spike.unittest.TestLoader") as loader,
            patch("run_langgraph_spike.unittest.TextTestRunner") as runner,
        ):
            loader.return_value.discover.return_value = suite
            runner.return_value.run.return_value = result
            self.assertEqual(run(self.report), 1)

    def test_incomplete_discovery_is_not_a_green_gate(self) -> None:
        result = SimpleNamespace(
            wasSuccessful=lambda: True, testsRun=0, skipped=[], failures=[], errors=[]
        )
        with (
            patch.dict(
                os.environ, {"COMFYUI_LANGGRAPH_SPIKE_ALLOWED": "1"}, clear=True
            ),
            patch("run_langgraph_spike.version", return_value="1.2.12"),
            patch("run_langgraph_spike.unittest.TextTestRunner") as runner,
        ):
            runner.return_value.run.return_value = result
            self.assertEqual(run(self.report), 1)
