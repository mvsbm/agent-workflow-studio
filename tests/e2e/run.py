"""Run the browser end-to-end tests against an isolated ComfyUI with the fake Pi."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fixture import REPO, ComfyFixture

HERE = Path(__file__).resolve().parent
TESTS = os.environ.get("E2E_ONLY", "").split() or [
    "build_and_run.cjs",
    "llm_record.cjs",
    "fuzzy_node.cjs",
    "agent_tool.cjs",
    "review_panel.cjs",
    "decision_rules.cjs",
    "matching_cascade.cjs",
    "api.cjs",
    "design.cjs",
]


def main() -> int:
    addons = os.environ.get("FAKE_PI_ADDONS", "1")
    env = {"AGENT_WORKFLOW_STUDIO_PI_CLI": str(HERE / "fake_pi.py"), "FAKE_PI_ADDONS": addons}
    failures = 0
    with ComfyFixture(env) as comfy:
        artifacts = comfy.root / "artifacts"
        artifacts.mkdir()
        for test in TESTS:
            result = subprocess.run(
                ["node", str(HERE / test)],
                cwd=REPO,
                env={
                    **os.environ,
                    "BASE": comfy.url,
                    "ARTIFACTS": str(artifacts),
                    "OUTPUT": str(comfy.root / "output"),
                },
                timeout=300,
            )
            if result.returncode:
                failures += 1
                print(f"FAIL {test} (add-ons={addons})\n--- ComfyUI log ---\n{comfy.log_tail()}")
        if failures or os.environ.get("E2E_KEEP"):
            kept = Path(os.environ.get("E2E_KEEP", "/tmp")) / f"aws-e2e-{comfy.port}"
            subprocess.run(["cp", "-R", str(artifacts), str(kept)], check=False)
            print(f"artifacts: {kept}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
