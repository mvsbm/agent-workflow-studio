#!/usr/bin/env bash
# Run the offline Python and JS tests against the installed ComfyUI (needs comfy_api).
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
COMFYUI_DIR="$APP_HOME/ComfyUI"

[[ -x "$COMFYUI_DIR/.venv/bin/python" ]] || { echo "Run install.sh first." >&2; exit 1; }

cd "$COMFYUI_DIR"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
    -s custom_nodes/agent_workflow_studio -t . -p 'test_*.py'
cd "$PACKAGE_ROOT"
for test in custom_nodes/agent_workflow_studio/web/test_*.mjs; do
    node --test "$test"
done
