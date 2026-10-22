#!/usr/bin/env bash
# Rebuild the shipped template workflows and their Templates-browser pictures in an isolated ComfyUI
# (never the user's instance). Afterwards: regenerate the blueprints (custom_nodes/.../blueprints.py).
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}/ComfyUI/.venv/bin/python"
"$PYTHON" - <<'PY'
import os, subprocess, sys
sys.path.insert(0, "tests/e2e")
from fixture import REPO, ComfyFixture
with ComfyFixture({}) as comfy:
    for script in ("tests/e2e/build_templates.cjs", "tests/e2e/build_reference.cjs", "tests/e2e/build_contacts.cjs", "tests/e2e/template_thumbnails.cjs"):
        done = subprocess.run(["node", script], cwd=REPO, env={**os.environ, "BASE": comfy.url}, timeout=900)
        if done.returncode:
            print(comfy.log_tail(40))
            sys.exit(done.returncode)
PY
