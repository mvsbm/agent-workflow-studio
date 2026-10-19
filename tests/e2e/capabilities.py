"""Run the capability tests in an isolated ComfyUI with the test-only node pack.

.venv/bin/python tests/e2e/capabilities.py   ->  tests/e2e/capabilities.json
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fixture import REPO, ComfyFixture  # noqa: E402

with ComfyFixture({}, packs=(HERE / "capability_pack",)) as comfy:
    done = subprocess.run(
        ["node", str(HERE / "capabilities.cjs")],
        cwd=REPO,
        env={**os.environ, "BASE": comfy.url},
        timeout=900,
    )
    if done.returncode == 2:
        print(comfy.log_tail(40))
sys.exit(done.returncode)
