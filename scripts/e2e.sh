#!/usr/bin/env bash
# Browser end-to-end tests: isolated ComfyUI (temp dir, free port) + fake Pi + Playwright.
#   scripts/e2e.sh            everything
#   scripts/e2e.sh platform   the platform assumptions (ComfyUI capabilities, Pi startup)
#   scripts/e2e.sh chat|data  one shard of the browser tests (tests/e2e/run.py SHARDS); CI runs them in parallel
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
[[ -x "$APP_HOME/ComfyUI/.venv/bin/python" ]] || { echo "Run install.sh first." >&2; exit 1; }

cd "$PACKAGE_ROOT"
[[ -d node_modules/playwright-core ]] || npm install --no-audit --no-fund --silent
# playwright-core 1.62.0 uses Chromium revision 1234; install it once if missing.
node -e "require('fs').accessSync(require('playwright-core').chromium.executablePath())" 2>/dev/null \
    || npx --no-install playwright-core install chromium

PART="${1:-all}"
PYTHON="$APP_HOME/ComfyUI/.venv/bin/python"
if [[ "$PART" == all || "$PART" == platform ]]; then
    # Platform assumptions first: if one fails, the tests built on it would fail confusingly.
    "$PYTHON" tests/e2e/capabilities.py
    (cd "$APP_HOME/ComfyUI" && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python "$PACKAGE_ROOT/tests/e2e/pi_startup.py")
fi
if [[ "$PART" != platform ]]; then
    SHARD=$([[ "$PART" == all ]] && echo "" || echo "$PART")
    E2E_SHARD="$SHARD" FAKE_PI_ADDONS=1 "$PYTHON" tests/e2e/run.py
    # Without SoL-Pi/Ponytail only the chat header differs (pi_startup checks both): the chat's own
    # test runs once more that way, not every test.
    if [[ "$PART" == all || "$PART" == chat ]]; then
        E2E_ONLY="build_and_run.cjs" FAKE_PI_ADDONS=0 "$PYTHON" tests/e2e/run.py
    fi
fi
