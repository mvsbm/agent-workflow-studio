import argparse
import hashlib
import json
import os
import sys
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

EXPECTED_VERSION = "1.2.12"
EXPECTED_CASES = frozenset(
    {
        "test_checkpoint_holds_data_not_permission",
        "test_forged_approval_is_rejected_before_framework_resume",
        "test_interrupted_node_reruns_but_completed_predecessor_does_not",
        "test_rejection_never_becomes_execution",
        "test_source_change_blocks_clarification_continuation",
    }
)


def case_ids(suite: unittest.TestSuite | unittest.TestCase) -> list[str]:
    if isinstance(suite, unittest.TestSuite):
        return [name for child in suite for name in case_ids(child)]
    return [suite.id()]


def run(report_path: Path) -> int:
    if os.environ.get("COMFYUI_LANGGRAPH_SPIKE_ALLOWED") != "1":
        print("spike_approval_required", file=sys.stderr)
        return 2
    try:
        installed = version("langgraph")
    except PackageNotFoundError:
        print("langgraph_spike_dependency_missing", file=sys.stderr)
        return 2
    if installed != EXPECTED_VERSION:
        print("langgraph_spike_version_mismatch", file=sys.stderr)
        return 2
    directory = (
        Path(__file__).resolve().parents[1] / "custom_nodes/agent_workflow_studio"
    )
    suite = unittest.TestLoader().discover(
        str(directory), pattern="test_langgraph_spike.py"
    )
    names = case_ids(suite)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    accepted = (
        result.wasSuccessful()
        and result.testsRun == len(EXPECTED_CASES)
        and len(names) == len(EXPECTED_CASES)
        and {name.rsplit(".", 1)[-1] for name in names} == EXPECTED_CASES
        and not result.skipped
    )
    report: dict[str, Any] = {
        "schema_version": "WORKFLOW_AGENT_RUNTIME_SPIKE_REPORT_V1",
        "sdk": "langgraph",
        "sdk_version": installed,
        "fixture": "deterministic-no-model",
        "synthetic": True,
        "production_ready": False,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "accepted": accepted,
        "case_ids": names,
        "source_sha256": {
            relative: hashlib.sha256(
                (directory.parents[1] / relative).read_bytes()
            ).hexdigest()
            for relative in (
                "ci/requirements-langgraph-spike.in",
                "ci/requirements-langgraph-spike.lock",
                "ci/run_langgraph_spike.py",
                "custom_nodes/agent_workflow_studio/test_langgraph_spike.py",
            )
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if accepted else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    return run(parser.parse_args().report)


if __name__ == "__main__":
    raise SystemExit(main())
