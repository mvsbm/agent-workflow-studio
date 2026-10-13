#!/usr/bin/env bash
# Browser end-to-end tests: isolated ComfyUI (temp dir, free port) + fake Pi + Playwright.
# Runs once with SoL-Pi/Ponytail reported and once without.
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
[[ -x "$APP_HOME/ComfyUI/.venv/bin/python" ]] || { echo "Run install.sh first." >&2; exit 1; }

cd "$PACKAGE_ROOT"
[[ -d node_modules/playwright-core ]] || npm install --no-audit --no-fund --silent
# playwright-core 1.62.0 uses Chromium revision 1234; install it once if missing.
node -e "require('fs').accessSync(require('playwright-core').chromium.executablePath())" 2>/dev/null \
    || npx --no-install playwright-core install chromium

for addons in 1 0; do
    FAKE_PI_ADDONS=$addons "$APP_HOME/ComfyUI/.venv/bin/python" tests/e2e/run.py
done
